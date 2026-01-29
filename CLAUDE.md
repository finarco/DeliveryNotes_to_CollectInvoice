# CLAUDE.md - AI Assistant Guide

This file provides context and guidelines for AI assistants working on the DeliveryNotes_to_CollectInvoice project.

## Project Overview

**Name:** DeliveryNotes_to_CollectInvoice
**Purpose:** A system for documenting and creating delivery notes for provided services/delivered goods, with subsequent generation of collective invoices (Evidencia a vytváranie dodacích listov za poskytnuté služby/dodaný tovar + následné generovanie zberných faktúr)
**License:** MIT
**Language Context:** Primary documentation in Slovak, code documentation in English

## Repository Structure

```
DeliveryNotes_to_CollectInvoice/
├── README.md           # Project description
├── LICENSE             # MIT License
└── CLAUDE.md           # This file - AI assistant guidelines
```

**Note:** This repository is in early development. The structure above will expand as the project grows.

## Core Business Domain

This application handles:
1. **Delivery Notes (Dodacie listy)** - Recording services provided or goods delivered
2. **Collective Invoices (Zberné faktúry)** - Generating consolidated invoices from multiple delivery notes

### Key Terminology
| Slovak | English | Description |
|--------|---------|-------------|
| Dodací list | Delivery Note | Document recording a single delivery/service |
| Zberná faktúra | Collective Invoice | Invoice combining multiple delivery notes |
| Služby | Services | Services provided to customers |
| Tovar | Goods | Physical products delivered |

## Development Guidelines

### Code Style & Conventions

When implementing features:
- Use **English** for all code (variables, functions, classes, comments)
- Use **Slovak** for user-facing text and labels (UI, reports, documents)
- Follow standard naming conventions for the chosen tech stack
- Keep functions focused and single-purpose
- Write meaningful commit messages in English

### Git Workflow

- Main branch: `main`
- Feature branches: `feature/<feature-name>`
- Bug fix branches: `fix/<issue-description>`
- Claude development branches: `claude/<session-id>`

**Commit Message Format:**
```
<type>: <short description>

<optional longer description>

Types: feat, fix, docs, refactor, test, chore
```

### Testing

- Write tests for business logic
- Focus on delivery note creation/validation
- Ensure invoice calculation accuracy
- Test edge cases for date ranges and aggregations

## For AI Assistants

### When Working on This Repository

1. **Understand the business context** - This is a Slovak business application dealing with delivery documentation and invoicing
2. **Preserve localization** - Keep Slovak text for user-facing content
3. **Maintain data integrity** - Financial calculations must be precise
4. **Follow existing patterns** - Match the style of existing code when adding features

### Key Questions to Consider

When implementing features, consider:
- How does this affect existing delivery notes?
- Does invoice generation need updating?
- Are there legal/compliance requirements for Slovak business documents?
- Is the data model supporting required reporting?

### Common Tasks

| Task | Approach |
|------|----------|
| Add new field to delivery note | Update model, migration, form, and validation |
| Modify invoice calculation | Update calculation logic with tests |
| Add reporting feature | Consider export formats (PDF, Excel) |
| UI changes | Keep consistent Slovak language |

## Architecture Notes

*(To be updated as the project develops)*

When choosing architecture, consider:
- Web-based for accessibility
- Database for persistent storage
- PDF generation for printable documents
- Multi-user support for business environment

## Environment Setup

*(To be documented when tech stack is chosen)*

## Dependencies

*(To be documented when dependencies are added)*

## Running the Application

*(To be documented when application scaffolding is created)*

## API Reference

*(To be documented when API is implemented)*

---

**Last Updated:** 2026-01-27
**Repository State:** Initial setup - awaiting implementation
