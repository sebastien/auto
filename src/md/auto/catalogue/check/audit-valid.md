---
kind: check
identity: auto:catalogue/check/audit-valid
title: Audit report is valid
description: >-
  Validates an Audit report against its artifact contract (scope, criteria,
  findings, counts and verdict) with `auto x audit validate`. Used in the Post
  of `auto:catalogue/artifact/audit`.
---
!?auto x audit validate {{ run.output }}
