---
name: validate-module
description: Run complete two-agent validation on module+tests (contract extraction + test validation). Binary pass/fail with specific issues.
---

# Module Validation Command

You are running the two-agent validation system to verify a module and its tests are production-ready.

## 🚨 CRITICAL: IMPLEMENTATION FILES ARE READ-ONLY

**YOU MUST NOT EDIT THE IMPLEMENTATION FILE BEING VALIDATED.**

During validation workflow:

**ALLOWED**:
- Read implementation file
- Edit TEST files to improve coverage/quality
- Add new tests
- Strengthen test assertions
- Fix test organization

**FORBIDDEN**:
- Edit implementation file
- Fix bugs in module code
- Refactor implementation
- Modify module behavior

**If validation reveals implementation bugs:**
1. Include in validation report
2. Present to user with note: "Implementation requires human review"
3. DO NOT fix implementation
4. Wait for user decision

**Validation tests the implementation AS-IS. User must decide on implementation changes.**

---

## Your Task

1. **Get module and test paths from user**
2. **Run contract-extractor agent** → Get contracts + validation checklist
3. **Run test-validator agent** → Get verdict (VALIDATED or BLOCKED)
4. **Report results clearly** → Present verdict + issues
5. **If implementation issues found** → Report to user, await decision

## Protocol

### Step 1: Get Paths

Ask user for:
- **Module path**: The Python module to validate
- **Test path**: The test file (or auto-detect from module path)

Auto-detect test path:
```
Module: tools/implementations/reminder_tool.py
→ Test: tests/tools/implementations/test_reminder_tool.py
```

### Step 2: Extract Contracts

Invoke contract-extractor agent:

```
Use Task tool:
subagent_type="contract-extractor"
description="Extract module contracts"
prompt="""Extract complete contract from module: [module_path]

Provide:
- Public interface (methods, signatures, types)
- Actual return structures (exact dict keys, types, constraints)
- Exception contracts (what raises what, when)
- Edge cases handled
- Dependencies and architectural concerns
- VALIDATION CHECKLIST with numbered requirements (R1, E1, EC1, S1, A1)

Module path: [module_path]"""
```

Wait for agent to complete. You will receive contract report with validation checklist.

### Step 3: Validate Tests

Invoke test-validator agent with contracts + test file:

```
Use Task tool:
subagent_type="test-validator"
description="Validate tests against contracts"
prompt="""Validate tests against extracted contracts:

MODULE: [module_path]
TESTS: [test_path]

CONTRACT REPORT:
[paste full contract report from Step 2]

Verify:
1. Contract Coverage - do tests cover all requirements from validation checklist?
2. Test Quality - are assertions strong? negative tests present?
3. Architecture - are design concerns acceptable?

Provide binary verdict: ✓ VALIDATED or ✗ BLOCKED
If blocked, list specific issues with file paths and line numbers."""
```

Wait for agent to complete. You will receive validation report with verdict.

### Step 4: Report Results

Present results to user in clear format:

**If ✓ VALIDATED:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ MODULE VALIDATED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Module: [module_path]
Tests: [test_path]

VALIDATION PASSED
─────────────────────────────────────────────────────

✓ Contract Coverage: [X%] (>= 95% required)
✓ Test Quality: [STRONG/GOOD]
✓ Architecture: [PASS/CONCERNS]

SUMMARY:
- All contracts tested
- Test quality verified
- Architecture sound
- Module is production-ready

[If any warnings exist, list them]
```

**If ✗ BLOCKED:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
❌ VALIDATION BLOCKED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Module: [module_path]
Tests: [test_path]

BLOCKING ISSUES ([count] issues must be fixed)
─────────────────────────────────────────────────────

[List each blocking issue from validation report]

Example:
1. R5 NOT COVERED: Missing test for result structure fields
   → Add test verifying each result has: message_id, content, match_score
   → File: tests/tools/implementations/test_conversationsearch_tool.py

2. E3 NOT COVERED: Missing test for ValueError on invalid message_id
   → Add test: test_expand_rejects_invalid_message_id()
   → File: tests/tools/implementations/test_conversationsearch_tool.py

VALIDATION SUMMARY:
─────────────────────────────────────────────────────
Contract Coverage: [X%] (95% required)
Test Quality: [rating]
Architecture: [status]

ACTION REQUIRED:
Fix the [count] blocking issues above and re-run validation.
```

### Step 5: Offer Re-validation

Ask user if they want to:
- Fix issues now and re-validate
- Review issues and validate later
- See full validation report details

## Mode Detection

**GUIDE MODE** (if tests don't exist):
```
User provides module path, but test file doesn't exist.

Response:
"No test file found at: [test_path]

Would you like to:
1. Use GUIDE MODE - I'll extract contracts and help you write tests
2. Specify a different test file path
3. Cancel validation"

If user chooses GUIDE MODE:
1. Extract contracts
2. Present validation checklist
3. Guide test writing
4. Offer to validate once tests written
```

**VALIDATION MODE** (if both exist):
```
Both module and tests exist → proceed with full validation
```

## Example Usage

## Complete Workflow

1. Ask user for module path (or use provided path)
2. Auto-detect test path: `tools/foo.py` → `tests/tools/test_foo.py`
3. Check if test file exists - if not, offer GUIDE MODE
4. Invoke contract-extractor with full prompt from Step 2
5. Invoke test-validator with full prompt from Step 3 + contract report
6. Parse validation report for verdict
7. Present results using format from Step 4

**For report formats, see**:
- contract-extractor.md for contract extraction format
- test-validator.md for validation report format

**Done**: Present clear verdict to user with actionable issues if blocked.