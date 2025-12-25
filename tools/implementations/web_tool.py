"""
Web tool providing search, fetch, and HTTP request capabilities.

Three operations:
- search: Query Kagi search API for current information
- fetch: Get cleaned webpage content with optional LLM extraction
- http: Make direct HTTP requests to APIs
"""
import logging
import re
from typing import Dict, Any, List, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from tools.repo import Tool
from tools.registry import registry
from utils import http_client

try:
    from kagiapi import KagiClient
    KAGI_AVAILABLE = True
except ImportError:
    KAGI_AVAILABLE = False

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False

try:
    from bs4 import BeautifulSoup, Comment
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


# --- Configuration ---

class WebToolConfig(BaseModel):
    """Configuration for web_tool."""
    enabled: bool = Field(default=True, description="Whether this tool is enabled")
    default_timeout: int = Field(default=30, description="Default timeout in seconds")
    max_timeout: int = Field(default=120, description="Maximum allowed timeout")
    # LLM config for content extraction (self-contained - not database-backed)
    llm_model: str = Field(default="openai/gpt-oss-20b", description="Model for content extraction")
    llm_endpoint: str = Field(default="https://api.groq.com/openai/v1/chat/completions", description="LLM endpoint")
    llm_api_key_name: Optional[str] = Field(default="provider_key", description="Vault key name for API key")


registry.register("web_tool", WebToolConfig)


# --- Input Models ---

class SearchInput(BaseModel):
    """Input for web search operation."""
    query: str = Field(..., min_length=1, description="Search query")
    max_results: int = Field(default=5, ge=1, le=20, description="Maximum results to return")
    allowed_domains: List[str] = Field(default_factory=list, description="Only include these domains")
    blocked_domains: List[str] = Field(default_factory=list, description="Exclude these domains")


class FetchInput(BaseModel):
    """Input for webpage fetch operation."""
    url: str = Field(..., description="URL to fetch")
    prompt: str = Field(
        default="Extract the main content from this webpage. Focus on article text, headings, and important information.",
        description="Extraction prompt for LLM"
    )
    format: Literal["text", "markdown", "html"] = Field(default="text", description="Output format")
    include_metadata: bool = Field(default=False, description="Include page metadata")
    timeout: Optional[int] = Field(default=None, ge=1, description="Request timeout in seconds")

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"URL must use http or https scheme, got: {parsed.scheme}")
        return v


class HttpInput(BaseModel):
    """Input for HTTP request operation."""
    method: Literal["GET", "POST", "PUT", "DELETE"] = Field(..., description="HTTP method")
    url: str = Field(..., description="Request URL")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Query parameters")
    headers: Optional[Dict[str, str]] = Field(default=None, description="HTTP headers")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Form data")
    json_body: Optional[Dict[str, Any]] = Field(default=None, description="JSON body")
    timeout: Optional[int] = Field(default=None, ge=1, description="Request timeout")
    response_format: Literal["json", "text", "full"] = Field(default="json", description="Response format")
    allowed_domains: List[str] = Field(default_factory=list, description="Only allow these domains")
    blocked_domains: List[str] = Field(default_factory=list, description="Block these domains")

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"URL must use http or https scheme, got: {parsed.scheme}")
        return v


# --- Tool Implementation ---

class WebTool(Tool):
    """
    Web tool combining search, fetch, and HTTP capabilities.

    Operations:
    - search: Query Kagi for current web information
    - fetch: Get cleaned content from webpages with LLM extraction
    - http: Make direct HTTP requests to APIs
    """

    name = "web_tool"
    description = "Web search, fetch webpages, and HTTP requests"

    anthropic_schema = {
        "name": "web_tool",
        "description": "Access the web: (1) search for current information via Kagi, (2) fetch and extract webpage content, (3) make HTTP requests to APIs",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["search", "fetch", "http"],
                    "description": "The operation to perform"
                },
                "query": {"type": "string", "description": "Search query (for search operation)"},
                "max_results": {"type": "integer", "description": "Max search results (default 5)"},
                "url": {"type": "string", "description": "URL for fetch/http operations"},
                "prompt": {"type": "string", "description": "Extraction prompt for fetch operation"},
                "format": {"type": "string", "enum": ["text", "markdown", "html"], "description": "Output format for fetch"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"], "description": "HTTP method"},
                "params": {"type": "object", "description": "Query parameters for http"},
                "headers": {"type": "object", "description": "HTTP headers"},
                "data": {"type": "object", "description": "Form data for http"},
                "json_body": {"type": "object", "description": "JSON body for http"},
                "timeout": {"type": "integer", "description": "Request timeout in seconds"},
                "response_format": {"type": "string", "enum": ["json", "text", "full"], "description": "Response format for http"},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Allowed domains filter"},
                "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Blocked domains filter"},
                "include_metadata": {"type": "boolean", "description": "Include page metadata in fetch"}
            },
            "required": ["operation"]
        }
    }

    # SSRF protection patterns
    _PRIVATE_NETWORK_PATTERNS = [
        r'^localhost$',
        r'^127\.',
        r'^10\.',
        r'^172\.(1[6-9]|2[0-9]|3[0-1])\.',
        r'^192\.168\.',
        r'^0\.0\.0\.0$',
        r'^\[::1\]$',
        r'^169\.254\.',
    ]

    def __init__(self):
        super().__init__()
        self._kagi: Optional[KagiClient] = None
        self._init_kagi()

    def run(self, **params) -> Dict[str, Any]:
        """Route to appropriate operation handler."""
        operation = params.pop("operation", None)
        if not operation:
            raise ValueError("Required parameter 'operation' not provided")

        if operation == "search":
            return self._search(SearchInput(**params))
        elif operation == "fetch":
            return self._fetch(FetchInput(**params))
        elif operation == "http":
            return self._http(HttpInput(**params))
        else:
            raise ValueError(f"Unknown operation: {operation}. Must be: search, fetch, or http")

    # --- Search Operation ---

    def _search(self, input: SearchInput) -> Dict[str, Any]:
        """Execute web search with Kagi primary, DuckDuckGo fallback."""
        # Try Kagi first if available
        if self._kagi:
            try:
                response = self._kagi.search(input.query, limit=input.max_results)
                results = []
                for item in response.get("data", []):
                    url = item.get("url", "")
                    if self._should_include_url(url, input.allowed_domains, input.blocked_domains):
                        results.append({
                            "title": item.get("title", ""),
                            "url": url,
                            "snippet": item.get("snippet", "")
                        })
                return {"success": True, "results": results, "provider": "kagi"}
            except Exception as e:
                self.logger.warning(f"Kagi search failed: {e}, falling back to DuckDuckGo")

        # Fall back to DuckDuckGo
        if not DDGS_AVAILABLE:
            raise ValueError(
                "Web search requires either Kagi API key or DuckDuckGo. "
                "Install ddgs: pip install ddgs"
            )

        try:
            ddgs = DDGS()
            raw_results = ddgs.text(
                input.query,
                max_results=input.max_results,
                backend="auto"
            )

            if not raw_results:
                self.logger.warning(f"DuckDuckGo returned no results for query: {input.query}")

            results = []
            for item in raw_results:
                url = item.get("href", "")
                if self._should_include_url(url, input.allowed_domains, input.blocked_domains):
                    results.append({
                        "title": item.get("title", ""),
                        "url": url,
                        "snippet": item.get("body", "")
                    })

            return {"success": True, "results": results, "provider": "duckduckgo"}
        except Exception as e:
            self.logger.error(f"DuckDuckGo search error: {e}")
            raise ValueError(f"DuckDuckGo search failed: {e}")

    # --- Fetch Operation ---

    def _fetch(self, input: FetchInput) -> Dict[str, Any]:
        """Fetch webpage, clean HTML, extract content via LLM."""
        self._validate_url(input.url)
        timeout = self._get_timeout(input.timeout)

        # Try Playwright first, fall back to HTTP
        html, response_info = self._fetch_page(input.url, timeout)
        if html is None:
            return {"success": False, "url": input.url, **response_info}

        # Clean HTML
        cleaned_html = self._clean_html(html)

        # Extract content via LLM
        extracted = self._extract_with_llm(cleaned_html, input.url, input.prompt, input.format)

        result = {"success": True, "url": input.url, "content": extracted}

        if input.include_metadata:
            result["title"] = self._extract_title(html)
            result["metadata"] = {
                "content_type": response_info.get("content_type", "text/html"),
                "size": len(html),
            }

        return result

    def _fetch_page(self, url: str, timeout: int) -> tuple:
        """Fetch page via Playwright or HTTP fallback. Returns (html, info_dict)."""
        # Try Playwright first
        try:
            from utils.playwright_service import PlaywrightService
            playwright = PlaywrightService.get_instance()
            html = playwright.fetch_rendered_html(url, timeout=timeout)
            return html, {"content_type": "text/html"}
        except ImportError:
            self.logger.info("Playwright not available, using HTTP fallback")
        except RuntimeError as e:
            if "chromium" in str(e).lower() or "executable" in str(e).lower():
                self.logger.info("Chromium not installed, using HTTP fallback")
            else:
                return None, {"error": "playwright_error", "message": str(e)}
        except TimeoutError as e:
            return None, {"error": "timeout", "message": str(e)}
        except Exception as e:
            self.logger.warning(f"Playwright failed: {e}, trying HTTP fallback")

        # HTTP fallback for static pages
        try:
            response = http_client.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0 (compatible; MIRA/1.0)"},
                follow_redirects=True
            )
            response.raise_for_status()
            return response.text, {"content_type": response.headers.get("Content-Type", "text/html")}
        except http_client.TimeoutException:
            return None, {"error": "timeout", "message": f"Request timed out after {timeout}s"}
        except http_client.HTTPStatusError as e:
            return None, {"error": "http_error", "message": f"HTTP {e.response.status_code}"}
        except Exception as e:
            return None, {"error": "request_error", "message": str(e)}

    def _clean_html(self, html: str) -> str:
        """Remove scripts, styles, and non-content elements."""
        if not BS4_AVAILABLE:
            self.logger.warning("BeautifulSoup not available, returning raw HTML")
            return html

        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content elements
        if soup.head:
            soup.head.decompose()
        for tag in soup.find_all(["script", "style", "noscript", "iframe", "object", "embed"]):
            tag.decompose()
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()

        return str(soup)

    # Max HTML length to send to LLM (conservative limit to avoid context overflow)
    # ~40k chars leaves room for system prompt, response, and safety margin
    _MAX_HTML_LENGTH = 40000

    def _extract_with_llm(self, html: str, url: str, prompt: str, format_type: str) -> str:
        """Use LLM to extract content from cleaned HTML."""
        from config import config
        from clients.vault_client import get_api_key
        from clients.llm_provider import LLMProvider

        # Get tool-specific LLM config
        tool_config = config.web_tool

        # Get API key (None for local providers like Ollama)
        if tool_config.llm_api_key_name:
            api_key = get_api_key(tool_config.llm_api_key_name)
            if not api_key:
                raise RuntimeError(f"API key '{tool_config.llm_api_key_name}' not found in Vault")
        else:
            api_key = None  # Local provider (Ollama) - no API key needed

        # Truncate HTML to prevent context length exceeded errors
        truncated = False
        original_length = len(html)
        if original_length > self._MAX_HTML_LENGTH:
            html = html[:self._MAX_HTML_LENGTH]
            truncated = True
            self.logger.info(f"Truncated HTML from {original_length} to {self._MAX_HTML_LENGTH} chars")

        format_instruction = {
            "markdown": "Format output as Markdown.",
            "html": "Return clean, filtered HTML.",
            "text": "Return as plain text."
        }.get(format_type, "")

        truncation_note = " NOTE: Content was truncated due to size." if truncated else ""
        system_prompt = f"""Extract information from this HTML per the user's request.
USER REQUEST: {prompt}
INSTRUCTIONS: Ignore navigation, ads, footers, sidebars. {format_instruction}{truncation_note}
SOURCE: {url}"""

        llm = LLMProvider()
        response = llm.generate_response(
            messages=[{"role": "user", "content": f"Extract from:\n```html\n{html}\n```"}],
            stream=False,
            endpoint_url=tool_config.llm_endpoint,
            model_override=tool_config.llm_model,
            api_key_override=api_key,
            system_override=system_prompt,
            temperature=0.1
        )

        return llm.extract_text_content(response)

    def _extract_title(self, html: str) -> str:
        """Extract page title from HTML."""
        match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else ""

    # --- HTTP Operation ---

    def _http(self, input: HttpInput) -> Dict[str, Any]:
        """Make HTTP request."""
        self._validate_url(input.url, input.allowed_domains, input.blocked_domains)
        timeout = self._get_timeout(input.timeout)

        # Build kwargs for the request
        kwargs = {
            "params": input.params,
            "headers": input.headers,
            "timeout": timeout,
            "follow_redirects": True
        }
        if input.data:
            kwargs["data"] = input.data
        if input.json_body:
            kwargs["json"] = input.json_body

        try:
            # Dispatch to appropriate http_client method
            method_map = {
                "GET": http_client.get,
                "POST": http_client.post,
                "PUT": http_client.put,
                "DELETE": http_client.delete
            }
            http_method = method_map[input.method]
            response = http_method(input.url, **kwargs)
        except http_client.TimeoutException:
            return {"success": False, "error": "timeout", "message": f"Timed out after {timeout}s"}
        except http_client.ConnectError as e:
            return {"success": False, "error": "connection_error", "message": str(e)}
        except http_client.HTTPStatusError as e:
            return {"success": False, "error": "http_error", "status_code": e.response.status_code, "message": str(e)}

        return self._format_http_response(response, input.response_format)

    def _format_http_response(self, response, format_type: str) -> Dict[str, Any]:
        """Format HTTP response based on requested format."""
        result = {
            "success": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "url": str(response.url)
        }

        if format_type == "json":
            try:
                result["data"] = response.json()
            except ValueError:
                result["data"] = response.text
                result["warning"] = "Response is not valid JSON"
        elif format_type == "text":
            result["data"] = response.text
        elif format_type == "full":
            result["data"] = response.text
            result["headers"] = dict(response.headers)
            try:
                result["json"] = response.json()
            except ValueError:
                pass

        return result

    # --- Helpers ---

    def _init_kagi(self) -> None:
        """Initialize Kagi client from vault."""
        if not KAGI_AVAILABLE:
            self.logger.info("Kagi library not available, will use DuckDuckGo for search")
            return

        try:
            from clients.vault_client import get_api_key
            api_key = get_api_key("kagi_api_key")
            if api_key:
                self._kagi = KagiClient(api_key)
                self.logger.info("Kagi client initialized")
            else:
                self.logger.info("Kagi API key not found, will use DuckDuckGo for search")
        except Exception as e:
            self.logger.warning(f"Failed to initialize Kagi: {e}, will use DuckDuckGo for search")

    def _get_timeout(self, timeout: Optional[int]) -> int:
        """Get validated timeout value."""
        from config import config
        try:
            default = config.web_tool.default_timeout
            max_timeout = config.web_tool.max_timeout
        except AttributeError:
            default, max_timeout = 30, 120

        if timeout is None:
            return default
        return min(timeout, max_timeout)

    def _validate_url(self, url: str, allowed: List[str] = None, blocked: List[str] = None) -> None:
        """Validate URL against SSRF protection and domain restrictions."""
        parsed = urlparse(url)
        # Use hostname (without port) for SSRF checks, netloc for domain matching
        hostname = (parsed.hostname or "").lower()
        domain = parsed.netloc.lower()

        # SSRF protection - always enforced (check hostname without port)
        for pattern in self._PRIVATE_NETWORK_PATTERNS:
            if re.match(pattern, hostname, re.IGNORECASE):
                raise ValueError(f"Blocked: private network access to {domain}")

        # Allowlist takes precedence
        if allowed:
            if not any(self._domain_matches(domain, d) for d in allowed):
                raise ValueError(f"Domain not in allowed list: {domain}")
        elif blocked:
            if any(self._domain_matches(domain, d) for d in blocked):
                raise ValueError(f"Domain blocked: {domain}")

    def _domain_matches(self, domain: str, pattern: str) -> bool:
        """Check if domain matches pattern (exact or subdomain)."""
        pattern = pattern.lower()
        return domain == pattern or domain.endswith("." + pattern)

    def _should_include_url(self, url: str, allowed: List[str], blocked: List[str]) -> bool:
        """Check if URL should be included in search results."""
        if not url:
            return False
        try:
            domain = urlparse(url).netloc.lower()
            if allowed:
                return any(self._domain_matches(domain, d) for d in allowed)
            if blocked:
                return not any(self._domain_matches(domain, d) for d in blocked)
            return True
        except Exception:
            return False
