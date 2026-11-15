---
kind: task
identity: auto:catalogue/task/check-site
title: Check the static site
description: >-
  Verifies a static website directory with the project's site check
  (`tools/check-site.sh`): pages present, well-formed HTML5, a shared
  stylesheet, resolvable internal links, a navigation linking every page and
  no scripts or external resources. Fixes the problems it reports. Use it
  once the pages and stylesheet of the site exist.
effects: [repo.read, repo.write, shell.run]
inputs:
  # Directory of the site, relative to the project
  dir: site
post: sh tools/check-site.sh {{ dir }}
---
## Process

The project's site check currently reports:

!sh tools/check-site.sh {{ dir }} 2>&1 || true

Fix every problem it reports by editing the files in `{{ dir }}/`, without
changing the content or design beyond what the fix requires, then run
`sh tools/check-site.sh {{ dir }}` again until it prints `CHECK-SITE: OK`.

Reply with the list of changes you made, and end with the last line printed
by the check.
