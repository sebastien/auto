---
title: Structural review
description: Review the organisation of modules, files and symbols against the domain
temperature: 0.1
inputs:
  # Source directory, relative to the project; a plain name stays a literal
  # (a `./` prefix would make it a path, and inline its content)
  src: src
params:
  # `analysis` reviews the structure; `full` adds the file-by-file review
  depth: analysis
output:
  format: markdown
  template: |
    ## Ontology
    ## Files
    ## Symbols
    ## Patterns
    ## File by file
    ## Recommendations
---
Review the structure of the code base in `{{ src }}`. The high-level goals for
the code base are:

- Domains and patterns are visible at the module and file level, down to the
  symbol level.
- Modules and files are clearly decomposed and structured, reflecting the
  domain and patterns: the file structure alone tells a lot about what the code
  base is about and what its components are.
- Similar things follow clear, consistent patterns, from file naming and
  structure to naming and API.

## Context

Source files (tracked by version control):

!if [ -d .jj ]; then jj file list {{ src }}; else git ls-files {{ src }}; fi | sort

Declared symbols:

!if [ -d .jj ]; then jj file list {{ src }}; else git ls-files {{ src }}; fi | sort | xargs sdk-symbols

## Preparation

1. From the source files, identify the components and patterns.
2. From the declared symbols, refine your understanding of the components and
   patterns.
3. From these two artifacts, identify the domain, components and patterns.
4. Build a small ontology mapping out the domain, based on file and symbol
   names (the *Ontology* section).

## Analysis

Review the following, highlighting issues, inconsistencies and possible
improvements:

1. File organisation and naming (the *Files* section)
2. Symbol organisation and naming (the *Symbols* section)
3. Existing patterns, their usage, consistency and improvements (the
   *Patterns* section)

{% if params.depth == "full" %}## Improvements

Review the source code file by file, based on the structure and findings
above (the *File by file* section). Group similar files so that related things
are compared together, and for each group:

1. Review consistency in naming and structure: symbol names match the
   conventions and ontology, and symbols are grouped consistently.
2. Review consistency in pattern usage: the same libraries and building
   blocks are used, and patterns are applied consistently.
3. Sum up the findings: issues, inconsistencies and improvements.

{% else %}Omit the *File by file* section: this review stops at the analysis.

{% endif %}## Rules

- Do not modify files: this is a review.
- Do not run the build system, checks, linters, tests or CI.
- End with *Recommendations*: the improvements to make, ordered by impact,
  each pointing to the files or symbols involved.
