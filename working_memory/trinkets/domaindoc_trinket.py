"""
Domaindoc Trinket - Injects enabled domain knowledge documents with section awareness.

Reads from SQLite storage and formats content with expand/collapse state.
Supports one level of nesting (sections with subsections).
Collapsed sections show only headers; expanded sections show full content.
When a parent is collapsed, ALL its subsections are hidden.
"""
import logging
from collections import defaultdict
from typing import Dict, Any, List, Optional

from working_memory.trinkets.base import EventAwareTrinket
from utils.user_context import get_current_user_id
from utils.userdata_manager import get_user_data_manager

logger = logging.getLogger(__name__)

# Threshold for "large section" warning
LARGE_SECTION_CHARS = 5000


class DomaindocTrinket(EventAwareTrinket):
    """
    Trinket that injects enabled domaindocs with section-level display.

    Reads from SQLite. Expanded sections show full content;
    collapsed sections show only headers with state indicator.
    """

    # Domaindocs are reference material that rarely changes - cache for efficiency
    cache_policy = True

    def _get_variable_name(self) -> str:
        """Return variable name for system prompt composition."""
        return "domaindoc"

    def generate_content(self, context: Dict[str, Any]) -> str:
        """
        Generate domaindoc content from enabled domains.

        Returns formatted domain content with section states,
        or empty string if no enabled domains.
        """
        user_id = context.get('user_id')
        if not user_id:
            try:
                user_id = get_current_user_id()
            except RuntimeError:
                return ""

        try:
            db = get_user_data_manager(user_id)
        except Exception as e:
            logger.warning(f"Failed to get user data manager: {e}")
            return ""

        # Get enabled domaindocs
        enabled_docs = db.fetchall(
            "SELECT * FROM domaindocs WHERE enabled = TRUE ORDER BY label"
        )

        if not enabled_docs:
            return ""

        domain_sections = []
        for doc_row in enabled_docs:
            doc = db._decrypt_dict(doc_row)
            section = self._format_domain_section(db, doc)
            if section:
                domain_sections.append(section)

        if not domain_sections:
            return ""

        return "<domain_knowledge>\n" + "\n".join(domain_sections) + "\n</domain_knowledge>"

    def _format_domain_section(
        self,
        db,
        doc: Dict[str, Any]
    ) -> str:
        """Format a single domain with its sections and subsections."""
        label = doc["label"]
        description = doc.get("encrypted__description", "")

        # Get ALL sections ordered by sort_order
        section_rows = db.fetchall(
            "SELECT * FROM domaindoc_sections WHERE domaindoc_id = :doc_id ORDER BY parent_section_id NULLS FIRST, sort_order",
            {"doc_id": doc["id"]}
        )
        all_sections = [db._decrypt_dict(row) for row in section_rows]

        if not all_sections:
            return ""

        # Separate top-level and group subsections by parent
        top_level = [s for s in all_sections if s.get("parent_section_id") is None]
        subsections_by_parent: Dict[int, List[Dict]] = defaultdict(list)
        for s in all_sections:
            parent_id = s.get("parent_section_id")
            if parent_id is not None:
                subsections_by_parent[parent_id].append(s)

        # Build section states list for guidance
        section_states = []
        for i, sec in enumerate(top_level):
            header = sec["header"]
            collapsed = sec.get("collapsed", False)
            expanded_by_default = sec.get("expanded_by_default", False)
            content_len = len(sec.get("encrypted__content", ""))
            subsecs = subsections_by_parent.get(sec["id"], [])
            sub_count = len(subsecs)

            # Build attributes for this section
            attrs = [f'header="{header}"']
            if i == 0:
                attrs.append('state="always_expanded"')
            elif collapsed:
                attrs.append('state="collapsed"')
                if expanded_by_default:
                    attrs.append('default="expanded"')
            else:
                if expanded_by_default:
                    attrs.append('state="expanded_by_default"')
                else:
                    attrs.append('state="expanded"')

            if sub_count > 0:
                attrs.append(f'subsections="{sub_count}"')
            elif content_len > LARGE_SECTION_CHARS:
                attrs.append('size="large"')

            # If parent is expanded and has subsections, nest them
            if not (collapsed and i > 0) and subsecs:
                section_states.append(f"<section {' '.join(attrs)}>")
                for sub in subsecs:
                    sub_header = sub["header"]
                    sub_collapsed = sub.get("collapsed", False)
                    sub_expanded_by_default = sub.get("expanded_by_default", False)
                    sub_len = len(sub.get("encrypted__content", ""))

                    sub_attrs = [f'header="{sub_header}"']
                    if sub_collapsed:
                        sub_attrs.append('state="collapsed"')
                        if sub_expanded_by_default:
                            sub_attrs.append('default="expanded"')
                    else:
                        if sub_expanded_by_default:
                            sub_attrs.append('state="expanded_by_default"')
                        else:
                            sub_attrs.append('state="expanded"')
                    if sub_len > LARGE_SECTION_CHARS:
                        sub_attrs.append('size="large"')
                    section_states.append(f"<subsection {' '.join(sub_attrs)}/>")
                section_states.append("</section>")
            else:
                section_states.append(f"<section {' '.join(attrs)}/>")

        section_states_text = "\n".join(section_states)

        # Build document content
        doc_content_parts = []
        for i, sec in enumerate(top_level):
            header = sec["header"]
            collapsed = sec.get("collapsed", False) and i > 0  # First section never collapsed
            content = sec.get("encrypted__content", "")
            content_len = len(content)
            subsecs = subsections_by_parent.get(sec["id"], [])
            sub_count = len(subsecs)

            if collapsed:
                # Show only header with collapsed indicator
                attrs = [f'header="{header}"', 'state="collapsed"']
                if sub_count > 0:
                    attrs.append(f'subsections="{sub_count}"')
                elif content_len > LARGE_SECTION_CHARS:
                    attrs.append('size="large"')
                doc_content_parts.append(f"<section {' '.join(attrs)}/>")
            else:
                # Show full content
                if content.strip():
                    doc_content_parts.append(f'<section header="{header}">')
                    doc_content_parts.append(content)
                else:
                    doc_content_parts.append(f'<section header="{header}">')

                # Show subsections if parent is expanded
                for sub in subsecs:
                    sub_header = sub["header"]
                    sub_collapsed = sub.get("collapsed", False)
                    sub_content = sub.get("encrypted__content", "")
                    sub_len = len(sub_content)

                    if sub_collapsed:
                        sub_attrs = [f'header="{sub_header}"', 'state="collapsed"']
                        if sub_len > LARGE_SECTION_CHARS:
                            sub_attrs.append('size="large"')
                        doc_content_parts.append(f"<subsection {' '.join(sub_attrs)}/>")
                    else:
                        if sub_content.strip():
                            doc_content_parts.append(f'<subsection header="{sub_header}">')
                            doc_content_parts.append(sub_content)
                            doc_content_parts.append("</subsection>")
                        else:
                            doc_content_parts.append(f'<subsection header="{sub_header}"/>')

                doc_content_parts.append("</section>")

        document_text = "\n".join(doc_content_parts)

        return f"""<domaindoc label="{label}">
<guidance>
<purpose>{description}</purpose>
<section_management>
<instruction>Sections support one level of nesting. When a parent is collapsed, ALL its subsections are hidden. First section is always expanded (overview). Use parent="X" to target subsections.</instruction>
<section_states>
{section_states_text}
</section_states>
<quick_reference>
<example operation="expand" section="NAME"/>
<example operation="expand" section="CHILD" parent="PARENT"/>
<example operation="create_section" section="NAME" parent="PARENT" content="..."/>
<example operation="reorder_sections" order="A,B" parent="PARENT"/>
</quick_reference>
</section_management>
</guidance>
<document>
{document_text if document_text.strip() else "<empty/>"}
</document>
</domaindoc>"""
