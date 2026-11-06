---
kind: check
identity: auto:catalogue/check/elaboration-valid
title: Elaboration is valid
description: >-
  Validates a TaskElaboration against the plan and its methodology, if
  declared, with `auto elaboration validate`. Used in the Post of Elaborators.
---
!?auto elaboration validate --plan {{ plan }} {{ run.output }}
