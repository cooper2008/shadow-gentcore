# Domain Knowledge Checklist

Fill this out BEFORE running `./ai learn` on your repo. It helps you think about what knowledge your agents need.

## Basic Info
- [ ] Domain name: _______________
- [ ] Primary language: _______________
- [ ] Framework: _______________
- [ ] Repository path: _______________

## Tooling
- [ ] Test framework: _______________
- [ ] Linter: _______________
- [ ] Type checker: _______________
- [ ] Package manager: _______________
- [ ] CI/CD system: _______________

## Architecture
- [ ] What pattern? (MVC, hexagonal, layered, etc.): _______________
- [ ] Key directories (where does code live?): _______________
- [ ] How are modules organized? _______________
- [ ] How are dependencies injected? _______________

## Coding Rules (your team's must-follow rules)
- [ ] _______________
- [ ] _______________
- [ ] _______________
- [ ] _______________

## Domain-Specific Rules
- [ ] Compliance requirements (PCI, HIPAA, GDPR, etc.): _______________
- [ ] Security rules: _______________
- [ ] Performance requirements: _______________
- [ ] Data handling rules: _______________

## What Should Agents Be Able To Do?
- [ ] Generate new features (endpoints, components, pipelines)?
- [ ] Write tests?
- [ ] Run linters and type checks?
- [ ] Review code against standards?
- [ ] Generate documentation?
- [ ] Other: _______________

## External Tools Needed
- [ ] GitHub (PR creation, issue tracking)?
- [ ] Jira (issue tracking)?
- [ ] Confluence (documentation)?
- [ ] Slack (notifications)?
- [ ] Other: _______________

## After filling this out:
```bash
# Option A: Auto-generate (the factory learns from your code + you augment with the above)
./ai learn /path/to/your-repo --domain-name <domain_name> --dry-run

# Option B: Fast path (write standards.md yourself, compose shared agents)
mkdir -p my_domain/context my_domain/workflows
# Write context/standards.md using the rules above
# Write workflows/main.yaml composing _shared/ agents
```
