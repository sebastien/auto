---
kind: check
identity: auto:catalogue/check/audit-resolution-valid
title: Audit resolution is valid
description: >-
  Validates an Audit resolution against its artifact contract (mode, selected
  findings, statuses and changes) with `auto x resolution validate`. Used in
  the Post of `auto:catalogue/artifact/audit-resolution`.
---
!?auto x resolution validate {{ run.output }}
