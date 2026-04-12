"""GenesisTestProvider — returns deterministic, schema-correct outputs per genesis agent.

Unlike DryRunProvider (which returns generic text), this provider returns
structured JSON that matches each genesis agent's output_schema. This enables
meaningful workflow testing: dependency injection, gate evaluation, and
output consistency across multiple runs.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from harness.providers.base_provider import BaseProvider, LLMChunk


# ── Deterministic outputs per genesis agent ────────────────────────────────

GENESIS_OUTPUTS: dict[str, dict[str, Any]] = {
    "SourceScannerAgent": {
        "reference_scan": {
            "standards_extracted": [
                {"name": "Python 3.11+", "description": "Uses Python 3.11 with type hints", "source": "pyproject.toml", "content_ref": "/mock/backend_fastapi/pyproject.toml"},
                {"name": "FastAPI framework", "description": "Async FastAPI with Pydantic v2", "source": "app/main.py", "content_ref": "/mock/backend_fastapi/app/main.py"},
                {"name": "Ruff linter", "description": "Ruff for linting and formatting", "source": "ruff.toml", "content_ref": "/mock/backend_fastapi/ruff.toml"},
                {"name": "Pytest async", "description": "pytest-asyncio for async test support", "source": "pyproject.toml", "content_ref": "/mock/backend_fastapi/pyproject.toml"},
            ],
            "patterns_found": [
                {"name": "Router pattern", "description": "Endpoints grouped by resource in app/routers/", "file_examples": ["app/routers/health.py", "app/routers/users.py"]},
                {"name": "Pydantic schemas", "description": "Request/response schemas in app/schemas/", "file_examples": ["app/schemas/user.py"]},
                {"name": "SQLAlchemy models", "description": "DB models in app/models/", "file_examples": ["app/models/user.py"]},
            ],
            "conventions": {
                "naming": "snake_case for functions/variables, PascalCase for classes, UPPER_CASE for constants",
                "imports": "stdlib first, third-party second, local third, separated by blank lines",
                "error_handling": "HTTPException for API errors, structured error responses with detail field",
                "testing": "test_{feature}_{scenario} naming, pytest-asyncio, fixtures in conftest.py",
            },
            "sample_files": [
                "/mock/backend_fastapi/app/main.py",
                "/mock/backend_fastapi/app/routers/health.py",
                "/mock/backend_fastapi/app/models/user.py",
                "/mock/backend_fastapi/tests/test_health.py",
                "/mock/backend_fastapi/ruff.toml",
            ],
        },
        "target_scan": {
            "tech_stack": {
                "language": "Python 3.11",
                "framework": "FastAPI",
                "test_framework": "pytest",
                "linter": "ruff",
                "package_manager": "pip",
                "key_dependencies": ["fastapi", "sqlalchemy", "alembic", "pydantic", "uvicorn"],
            },
            "file_structure": {
                "source_dirs": ["app/", "app/routers/", "app/models/", "app/schemas/"],
                "test_dirs": ["tests/"],
                "config_files": ["pyproject.toml", "ruff.toml", "Dockerfile", "alembic.ini"],
            },
            "tools_used": ["pytest", "ruff", "alembic", "docker", "github-actions"],
            "workflow_processes": [
                {"name": "Feature Development", "stages": ["code", "lint", "test", "review"]},
                {"name": "Database Migration", "stages": ["create_migration", "review", "apply", "verify"]},
                {"name": "CI/CD Pipeline", "stages": ["build", "test", "lint", "deploy"]},
            ],
        },
        "docs_scan": {
            "inventory": [
                {"source": "/mock/docs", "title": "ARCHITECTURE_DIAGRAM.md", "document_type": "reference", "content_ref": "/mock/docs/ARCHITECTURE_DIAGRAM.md", "confidence": 0.90},
                {"source": "/mock/docs", "title": "TEAM_GUIDE.md", "document_type": "procedure", "content_ref": "/mock/docs/TEAM_GUIDE.md", "confidence": 0.85},
            ],
            "content_refs": ["/mock/docs/ARCHITECTURE_DIAGRAM.md", "/mock/docs/TEAM_GUIDE.md"],
        },
        "scan_quality": {
            "reference_depth": 85.0,
            "target_coverage": 90.0,
            "docs_coverage": 60.0,
            "overall": 78.3,
        },
    },

    "KnowledgeMapperAgent": {
        "knowledge_map": {
            "standards_sources": [
                {"title": "pyproject.toml", "content_ref": "file://pyproject.toml"},
                {"title": "ruff.toml", "content_ref": "file://ruff.toml"},
                {"title": "requirements.txt", "content_ref": "file://requirements.txt"},
                {"title": "Dockerfile", "content_ref": "file://Dockerfile"},
            ],
            "reference_topics": [
                {"topic": "FastAPI Application Structure", "sources": ["app/main.py", "app/models/user.py"]},
                {"topic": "API Documentation", "sources": ["README.md"]},
            ],
            "workflow_processes": [
                {"name": "Feature Development", "stages": ["code", "lint", "test", "review", "deploy"]},
                {"name": "Database Migration", "stages": ["create_migration", "review", "apply", "verify"]},
                {"name": "CI/CD Pipeline", "stages": ["build", "test", "lint", "deploy"]},
            ],
            "compliance_rules": [
                {"name": "Code Quality", "source": "ruff.toml", "description": "Ruff linting rules enforced"},
            ],
            "output_templates": [
                {"name": "Test File Template", "source": "tests/test_health.py"},
                {"name": "Schema Template", "source": "app/schemas/user.py"},
            ],
            "glossary_terms": [
                {"term": "FastAPI", "definition": "Modern Python web framework for building APIs"},
                {"term": "Alembic", "definition": "Database migration tool for SQLAlchemy"},
                {"term": "Pydantic", "definition": "Data validation library using Python type hints"},
            ],
            "roles": [
                {"name": "Backend Developer", "responsibilities": ["Write API endpoints", "Write tests", "Run migrations"]},
            ],
            "training_content": [],
            "examples": [],
            "unclassified": [],
        },
        "coverage": {
            "standards": 80.0,
            "workflows": 75.0,
            "compliance": 40.0,
            "tools": 60.0,
            "roles": 50.0,
            "overall": 61.0,
        },
        "gaps": [
            {"category": "compliance", "description": "No security scanning policies found", "severity": "warning", "suggestion": "Add security scanning configuration"},
            {"category": "tools", "description": "CI/CD tool integrations not fully documented", "severity": "info", "suggestion": "Document deployment tools and procedures"},
        ],
    },

    "ToolDiscoveryAgent": {
        "tools_discovered": [
            {"name": "pytest", "system": "pytest", "purpose": "Python test runner", "status": "available", "integration": "tool_pack"},
            {"name": "ruff", "system": "ruff", "purpose": "Python linter", "status": "available", "integration": "tool_pack"},
            {"name": "alembic", "system": "alembic", "purpose": "Database migrations", "status": "available", "integration": "tool_pack"},
            {"name": "uvicorn", "system": "uvicorn", "purpose": "ASGI server", "status": "available", "integration": "tool_pack"},
            {"name": "docker", "system": "docker", "purpose": "Container runtime", "status": "available", "integration": "shell_command"},
            {"name": "gh", "system": "GitHub CLI", "purpose": "GitHub integration", "status": "available", "integration": "tool_pack"},
        ],
        "mcp_config": "servers:\n  - name: context7\n    command: npx -y @anthropic/context7-mcp\n    transport: stdio\n    tools: [query-docs, resolve-library-id]\n",
        "tool_packs": ["toolpack://core/filesystem", "toolpack://core/shell", "toolpack://core/search", "toolpack://services/github"],
        "gaps": ["No PagerDuty integration found", "No Jira integration found"],
        "discovery_quality": {
            "tools_matched_pct": 85.7,
            "tools_available_pct": 100.0,
        },
    },

    "ContextEngineerAgent": {
        "documents": {
            "standards_md": (
                "# Backend FastAPI Standards\n\n"
                "## Language & Framework\n- Python 3.11+\n- FastAPI with async/await\n- Pydantic v2 for schemas\n\n"
                "## Code Quality\n- Linter: ruff (see ruff.toml)\n- Type hints required on all functions\n- Docstrings on public APIs\n\n"
                "## Testing\n- Framework: pytest with pytest-asyncio\n- Minimum coverage: 80%\n- Test naming: test_{feature}_{scenario}\n\n"
                "## Database\n- ORM: SQLAlchemy 2.0 async\n- Migrations: Alembic\n- Naming: snake_case tables and columns\n\n"
                "## API Design\n- RESTful endpoints under /v1/\n- Pydantic schemas for request/response\n- HTTP status codes per RFC 7231\n\n"
                "## Deployment\n- Docker containerized\n- GitHub Actions CI/CD\n- Health endpoint: GET /v1/health\n"
            ),
            "glossary_md": (
                "# Glossary\n\n"
                "- **FastAPI**: Modern Python web framework for building APIs with automatic OpenAPI docs\n"
                "- **Alembic**: Database migration tool for SQLAlchemy\n"
                "- **Pydantic**: Data validation library using Python type hints\n"
                "- **ASGI**: Asynchronous Server Gateway Interface\n"
                "- **SQLAlchemy**: Python SQL toolkit and ORM\n"
            ),
            "reference_docs": [
                {
                    "filename": "fastapi_patterns.md",
                    "content": "# FastAPI Patterns\n\n## Router Structure\nGroup endpoints by resource under app/routers/.\n\n## Dependency Injection\nUse FastAPI Depends() for database sessions, auth, etc.\n\n## Error Handling\nUse HTTPException with appropriate status codes.\n",
                    "topic": "FastAPI Application Structure",
                    "depth_score": 72.0,
                },
                {
                    "filename": "database_patterns.md",
                    "content": "# Database Patterns\n\n## Models\nDefine in app/models/ using SQLAlchemy declarative base.\n\n## Migrations\nCreate with: alembic revision --autogenerate -m 'description'\nApply with: alembic upgrade head\n",
                    "topic": "Database Management",
                    "depth_score": 65.0,
                },
            ],
            "compliance_draft": (
                "# Compliance Rules\n\n"
                "sensitive_patterns:\n"
                "  - '(api[_-]?key|secret[_-]?key|password)\\\\s*[:=]\\\\s*[\\'\"][^\\'\"]{8,}[\\'\"]'\n"
                "  - 'DATABASE_URL.*://.*:.*@'\n\n"
                "blocked_actions:\n"
                "  - 'Deploy without passing tests'\n"
                "  - 'Merge without code review'\n"
            ),
        },
        "quality_scores": {
            "standards_completeness": 78.0,
            "reference_depth": 68.5,
            "compliance_coverage": 45.0,
            "glossary_coverage": 60.0,
            "overall": 62.9,
        },
        "generation_notes": [
            "Standards.md covers core conventions (132 lines, within 500-line limit)",
            "Generated 2 reference docs from identified topics",
            "Compliance coverage is low — only code quality rules found, no security policies",
        ],
    },

    "AgentArchitectAgent": {
        "agent_roster": [
            {
                "name": "CodeWriterAgent",
                "version": "1.0.0",
                "description": "Writes production FastAPI code following domain standards. Context-driven — adapts via injected standards.md.",
                "purpose": "Write FastAPI endpoints, models, and schemas following standards",
                "category": "fast-codegen",
                "execution_mode": {"primary": "plan_execute", "fallback": "react", "max_plan_steps": 8, "max_react_steps": 15},
                "tools": [
                    {"name": "file_write", "description": "Write generated code to files", "pack": "toolpack://core/filesystem"},
                    {"name": "file_read", "description": "Read existing code for patterns", "pack": "toolpack://core/filesystem"},
                    {"name": "search_code", "description": "Search codebase for imports, base classes", "pack": "toolpack://core/search"},
                ],
                "permissions": {"file_edit": "allow", "file_create": "allow", "shell_command": "deny", "network_access": "deny"},
                "constraints": {"max_file_size_kb": 500, "forbidden_patterns": ["eval(", "exec(", "__import__"]},
                "input_schema": {"type": "object", "required": ["task_description"], "properties": {"task_description": {"type": "string"}, "target_directory": {"type": "string", "default": "src/"}}},
                "output_schema": {"type": "object", "required": ["files_created", "files_modified", "summary"], "properties": {"files_created": {"type": "array", "items": {"type": "string"}}, "files_modified": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}},
                "compliance_constraints": ["Must follow naming conventions in standards.md", "No eval/exec/dynamic imports"],
                "harness": {"gate_condition": "status == success", "gate_on_fail": "retry", "max_retries": 2, "fallback_step": None, "grading_threshold": 0.75},
            },
            {
                "name": "TestRunnerAgent",
                "version": "1.0.0",
                "description": "Runs pytest suite and reports pass/fail with coverage. Read-only — never modifies code.",
                "purpose": "Run pytest suite and report results",
                "category": "reasoning",
                "execution_mode": {"primary": "react", "max_react_steps": 10},
                "tools": [
                    {"name": "shell_exec", "description": "Execute pytest commands", "pack": "toolpack://core/shell"},
                    {"name": "file_read", "description": "Read test files and output", "pack": "toolpack://core/filesystem"},
                ],
                "permissions": {"file_edit": "deny", "file_create": "deny", "shell_command": "allow", "network_access": "deny"},
                "constraints": {"read_only": True, "allowed_commands": ["pytest", "python -m pytest"]},
                "input_schema": {"type": "object", "required": ["test_target"], "properties": {"test_target": {"type": "string", "default": "tests/"}}},
                "output_schema": {"type": "object", "required": ["tests_passed", "test_count", "failures", "summary"], "properties": {"tests_passed": {"type": "boolean"}, "test_count": {"type": "integer"}, "failures": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}}},
                "compliance_constraints": [],
                "harness": {"gate_condition": "tests_passed == true", "gate_on_fail": "retry", "max_retries": 2, "fallback_step": "code", "grading_threshold": 0.80},
            },
            {
                "name": "LinterAgent",
                "version": "1.0.0",
                "description": "Runs ruff linter and format checker against domain ruff.toml configuration.",
                "purpose": "Run ruff linter and format checker",
                "category": "reasoning",
                "execution_mode": {"primary": "react", "max_react_steps": 8},
                "tools": [
                    {"name": "shell_exec", "description": "Execute ruff check/format commands", "pack": "toolpack://core/shell"},
                    {"name": "file_read", "description": "Read lint config and source files", "pack": "toolpack://core/filesystem"},
                ],
                "permissions": {"file_edit": "deny", "file_create": "deny", "shell_command": "allow", "network_access": "deny"},
                "constraints": {"read_only": True, "allowed_commands": ["ruff check", "ruff format --check"]},
                "input_schema": {"type": "object", "properties": {"target_path": {"type": "string", "default": "."}}},
                "output_schema": {"type": "object", "required": ["lint_passed", "issues_count", "issues", "summary"], "properties": {"lint_passed": {"type": "boolean"}, "issues_count": {"type": "integer"}, "issues": {"type": "array", "items": {"type": "object", "properties": {"file": {"type": "string"}, "line": {"type": "integer"}, "rule": {"type": "string"}, "message": {"type": "string"}}}}, "summary": {"type": "string"}}},
                "compliance_constraints": ["Must use ruff configuration from ruff.toml"],
                "harness": {"gate_condition": "lint_passed == true", "gate_on_fail": "retry", "max_retries": 1, "fallback_step": "code", "grading_threshold": 0.80},
            },
            {
                "name": "ReviewerAgent",
                "version": "1.0.0",
                "description": "Reviews code changes against domain standards. Produces structured findings with severity levels and actionable feedback.",
                "purpose": "Review code changes against standards and best practices",
                "category": "reasoning",
                "execution_mode": {"primary": "chain_of_thought"},
                "tools": [
                    {"name": "file_read", "description": "Read source files and standards", "pack": "toolpack://core/filesystem"},
                    {"name": "search_code", "description": "Search for patterns and anti-patterns", "pack": "toolpack://core/search"},
                ],
                "permissions": {"file_edit": "deny", "file_create": "deny", "shell_command": "deny", "network_access": "deny"},
                "constraints": {"read_only": True},
                "input_schema": {"type": "object", "required": ["files_to_review"], "properties": {"files_to_review": {"type": "array", "items": {"type": "string"}}, "review_focus": {"type": "string", "description": "Optional focus area: naming, architecture, security, tests"}}},
                "output_schema": {"type": "object", "required": ["approved", "score", "comments", "summary"], "properties": {"approved": {"type": "boolean"}, "score": {"type": "number", "minimum": 0, "maximum": 1}, "comments": {"type": "array", "items": {"type": "object", "properties": {"file": {"type": "string"}, "line": {"type": "integer"}, "severity": {"type": "string", "enum": ["error", "warning", "info"]}, "category": {"type": "string"}, "message": {"type": "string"}}}}, "summary": {"type": "string"}}},
                "compliance_constraints": ["Review against context/standards.md", "Check naming conventions", "Verify error handling patterns", "Ensure test coverage for new code"],
                "harness": {"gate_condition": "approved == true", "gate_on_fail": "retry", "max_retries": 1, "fallback_step": "code", "grading_threshold": 0.75},
            },
            {
                "name": "MigrationAgent",
                "version": "1.0.0",
                "description": "Creates and applies Alembic database migrations. Ensures migrations are reversible.",
                "purpose": "Create and apply Alembic database migrations",
                "category": "fast-codegen",
                "execution_mode": {"primary": "plan_execute", "max_plan_steps": 5},
                "tools": [
                    {"name": "shell_exec", "description": "Run alembic commands", "pack": "toolpack://core/shell"},
                    {"name": "file_read", "description": "Read models and existing migrations", "pack": "toolpack://core/filesystem"},
                    {"name": "file_write", "description": "Write migration files", "pack": "toolpack://core/filesystem"},
                ],
                "permissions": {"file_edit": "allow", "file_create": "allow", "shell_command": "allow", "network_access": "deny"},
                "constraints": {"allowed_commands": ["alembic revision", "alembic upgrade", "alembic downgrade", "alembic history"]},
                "input_schema": {"type": "object", "required": ["migration_description"], "properties": {"migration_description": {"type": "string"}}},
                "output_schema": {"type": "object", "required": ["migration_file", "applied", "reversible", "summary"], "properties": {"migration_file": {"type": "string"}, "applied": {"type": "boolean"}, "reversible": {"type": "boolean"}, "summary": {"type": "string"}}},
                "compliance_constraints": ["Must create reversible migrations", "Must not drop columns without explicit approval"],
                "harness": {"gate_condition": "status == success", "gate_on_fail": "retry", "max_retries": 1, "fallback_step": None, "grading_threshold": 0.80},
            },
        ],
        "workflow_design": {
            "name": "feature_development",
            "steps": [
                {"name": "code", "agent": "CodeWriterAgent", "description": "Write feature code"},
                {"name": "lint", "agent": "LinterAgent", "depends_on": ["code"], "description": "Lint check"},
                {"name": "test", "agent": "TestRunnerAgent", "depends_on": ["code"], "description": "Run tests"},
                {"name": "review", "agent": "ReviewerAgent", "depends_on": ["lint", "test"], "description": "Code review"},
            ],
            "gates": [
                {"step": "lint", "condition": "lint_passed == true", "on_fail": "retry", "max_retries": 2},
                {"step": "test", "condition": "tests_passed == true", "on_fail": "retry", "max_retries": 2},
                {"step": "review", "condition": "approved == true", "on_fail": "retry", "max_retries": 1},
            ],
            "feedback_loops": [
                {"from_step": "review", "to_step": "code", "condition": "review.approved == false", "max_iterations": 2},
            ],
            "parallel_branches": [["lint", "test"]],
            "budget": {"max_tokens": 200000, "max_cost_usd": 10.0, "max_duration_seconds": 1200},
        },
        "tool_assignments": {
            "CodeWriterAgent": ["file_write", "file_read", "search_code"],
            "TestRunnerAgent": ["shell_exec", "file_read"],
            "LinterAgent": ["shell_exec", "file_read"],
            "ReviewerAgent": ["file_read", "search_code"],
            "MigrationAgent": ["shell_exec", "file_read", "file_write"],
        },
        "grading_specs": [
            {
                "agent_name": "CodeWriterAgent",
                "automated_criteria": [
                    {"name": "files_created", "check": "files_created_count >= 1", "weight": 0.20},
                    {"name": "no_forbidden_patterns", "check": "forbidden_pattern_count == 0", "weight": 0.15},
                    {"name": "type_annotations", "check": "type_hint_coverage >= 0.9", "weight": 0.20},
                ],
                "llm_judge_criteria": [
                    {"name": "follows_standards", "prompt": "Does this code follow ALL coding standards from context/standards.md? Check naming, imports, error handling, typing.", "weight": 0.30},
                    {"name": "matches_architecture", "prompt": "Does this code follow the FastAPI architecture patterns? Router structure, dependency injection, Pydantic schemas.", "weight": 0.15},
                ],
                "pass_threshold": 0.75,
            },
            {
                "agent_name": "TestRunnerAgent",
                "automated_criteria": [
                    {"name": "tests_ran", "check": "test_count >= 1", "weight": 0.50},
                    {"name": "exit_code", "check": "tests_passed == true", "weight": 0.50},
                ],
                "llm_judge_criteria": [],
                "pass_threshold": 0.80,
            },
            {
                "agent_name": "LinterAgent",
                "automated_criteria": [
                    {"name": "lint_clean", "check": "lint_passed == true", "weight": 0.60},
                    {"name": "issues_low", "check": "issues_count <= 5", "weight": 0.40},
                ],
                "llm_judge_criteria": [],
                "pass_threshold": 0.80,
            },
            {
                "agent_name": "ReviewerAgent",
                "automated_criteria": [
                    {"name": "has_score", "check": "score >= 0", "weight": 0.10},
                    {"name": "has_comments", "check": "true", "weight": 0.10},
                ],
                "llm_judge_criteria": [
                    {"name": "naming_conventions", "prompt": "Does the review check naming conventions (snake_case functions, PascalCase classes, UPPER_CASE constants)?", "weight": 0.20},
                    {"name": "error_handling", "prompt": "Does the review check error handling patterns (HTTPException, proper status codes, error responses)?", "weight": 0.20},
                    {"name": "test_coverage", "prompt": "Does the review verify that new code has corresponding tests?", "weight": 0.20},
                    {"name": "review_actionable", "prompt": "Are review comments specific, actionable, and tied to file/line numbers?", "weight": 0.20},
                ],
                "pass_threshold": 0.75,
            },
            {
                "agent_name": "MigrationAgent",
                "automated_criteria": [
                    {"name": "file_created", "check": "migration_file != ''", "weight": 0.40},
                    {"name": "is_reversible", "check": "reversible == true", "weight": 0.40},
                ],
                "llm_judge_criteria": [
                    {"name": "migration_safe", "prompt": "Is this migration safe? No data loss, no long locks, reversible?", "weight": 0.20},
                ],
                "pass_threshold": 0.80,
            },
        ],
        "design_quality": {
            "process_coverage_pct": 80.0,
            "dag_valid": True,
            "agent_count": 5,
            "has_feedback_loops": True,
            "has_compliance_gates": True,
        },
    },

    "AgentBuilderAgent": {
        "domain_dir": "./output/backend_fastapi",
        "files_created": [
            "domain.yaml",
            "context/standards.md",
            "context/glossary.md",
            "context/reference/fastapi_patterns.md",
            "context/reference/database_patterns.md",
            "agents/CodeWriterAgent/v1/agent_manifest.yaml",
            "agents/CodeWriterAgent/v1/system_prompt.md",
            "agents/CodeWriterAgent/v1/grading_criteria.yaml",
            "agents/TestRunnerAgent/v1/agent_manifest.yaml",
            "agents/TestRunnerAgent/v1/system_prompt.md",
            "agents/TestRunnerAgent/v1/grading_criteria.yaml",
            "agents/LinterAgent/v1/agent_manifest.yaml",
            "agents/LinterAgent/v1/system_prompt.md",
            "agents/LinterAgent/v1/grading_criteria.yaml",
            "agents/ReviewerAgent/v1/agent_manifest.yaml",
            "agents/ReviewerAgent/v1/system_prompt.md",
            "agents/ReviewerAgent/v1/grading_criteria.yaml",
            "agents/MigrationAgent/v1/agent_manifest.yaml",
            "agents/MigrationAgent/v1/system_prompt.md",
            "agents/MigrationAgent/v1/grading_criteria.yaml",
            "workflows/feature_development.yaml",
            "tools/mcp_servers.yaml",
            "rules/compliance.yaml",
        ],
        "files_failed": [],
        "agents_created": ["CodeWriterAgent", "TestRunnerAgent", "LinterAgent", "ReviewerAgent", "MigrationAgent"],
        "workflows_created": ["feature_development"],
        "build_quality": {
            "files_planned": 23,
            "files_written": 23,
            "completion_pct": 100.0,
        },
    },

    "QualityGateAgent": {
        "validation_passed": True,
        "overall_score": 82.0,
        "issues": {
            "structural": [],
            "completeness": [],
            "coherence": ["ReviewerAgent grading_criteria could be more specific"],
            "dry_run": [],
            "cross_reference": [],
        },
        "targeted_feedback": [
            {
                "target_file": "agents/ReviewerAgent/v1/grading_criteria.yaml",
                "target_agent": "AgentBuilder",
                "issue": "LLM judge criteria prompt is generic",
                "fix_suggestion": "Add specific review criteria: naming conventions, error handling, test coverage",
                "severity": "warning",
            },
        ],
        "agent_scores": {
            "CodeWriterAgent": 85.0,
            "TestRunnerAgent": 90.0,
            "LinterAgent": 88.0,
            "ReviewerAgent": 70.0,
            "MigrationAgent": 82.0,
        },
    },

    "EvolutionAgent": {
        "run_summary": {
            "total_runs": 0,
            "success_rate": 0.0,
            "avg_score": 0.0,
            "most_failed_agent": "none",
        },
        "improvements": [],
        "domain_health": {
            "overall_score": 82.0,
            "trend": "stable",
            "critical_issues": 0,
        },
    },
}


def _identify_agent(messages: list[dict[str, Any]]) -> str:
    """Extract genesis agent name from system message."""
    for msg in messages:
        if msg.get("role") == "system":
            content = str(msg.get("content", ""))
            for agent_name in GENESIS_OUTPUTS:
                if agent_name in content:
                    return agent_name
    return ""


class GenesisTestProvider(BaseProvider):
    """Returns deterministic, schema-correct outputs for genesis agents.

    Each genesis agent gets a pre-defined output that matches its output_schema.
    This enables meaningful workflow testing: dependency injection works because
    outputs contain real structured data that downstream agents can reference.
    """

    def __init__(self) -> None:
        self.call_log: list[dict[str, Any]] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        agent_name = _identify_agent(messages)

        self.call_log.append({
            "agent": agent_name,
            "message_count": len(messages),
        })

        if agent_name and agent_name in GENESIS_OUTPUTS:
            output = GENESIS_OUTPUTS[agent_name]
            return {
                "content": json.dumps(output, indent=2),
                "tokens_used": 0,
                "tool_calls": [],
                "model": "genesis-test",
            }

        # Fallback for unknown agents
        return {
            "content": json.dumps({"status": "completed", "output": "test output"}),
            "tokens_used": 0,
            "tool_calls": [],
            "model": "genesis-test",
        }

    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> AsyncIterator[LLMChunk]:
        response = await self.chat(messages, **kwargs)
        yield LLMChunk(
            content=response["content"],
            delta=response["content"],
            is_final=True,
            tokens_used=0,
        )

    @property
    def provider_name(self) -> str:
        return "genesis_test"

    @property
    def default_model(self) -> str:
        return "genesis-test"
