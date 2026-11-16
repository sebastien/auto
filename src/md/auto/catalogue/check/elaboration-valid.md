---
kind: check
identity: auto:catalogue/check/elaboration-valid
title: Elaboration is valid
description: >-
  Validates a TaskElaboration against the plan and its methodology, if
  declared, with `auto x elaboration validate`. Used in the Post of Elaborators.
---
!?auto x elaboration validate --plan {{ plan }} --task {{ target | default: "root" }} {{ run.output }}
