---
kind: artifact
identity: auto:catalogue/artifact/memory-consolidation
title: Memo memory consolidation
description: A revision-checked Memo change set and nonblocking report.
output:
  format: json
  schema:
    type: object
    additionalProperties: false
    required: [id, agent, snapshot, changes, report]
    properties:
      id: {type: string, minLength: 1}
      agent: {type: string, minLength: 1}
      snapshot: {type: string, pattern: '^sha256:[0-9a-f]{64}$'}
      changes: {type: array, items: {type: object}}
      report:
        type: object
        additionalProperties: false
        required: [summary]
        properties:
          summary: {type: string, minLength: 1}
          deferred: {type: array, items: {type: object}}
          acknowledged: {type: array, items: {type: string}}
          questions: {type: array, items: {type: object}}
          proposals: {type: array, items: {type: object}}
---
# Memo memory consolidation

Memo's dry consolidation validator owns the detailed change-set rules, source
revision checks, world ownership and complete proposed-state validation.
