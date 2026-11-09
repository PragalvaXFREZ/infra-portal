---
title: CI, one step at a time
kind: lab
date: 2026-10-09
link: https://github.com/PragalvaXFREZ/dwit-in-house-infra/tree/main/learning/ci
summary: Build a small tool, decide how to check it, then automate those checks.
---
The project is a Python CLI that validates a fictional server inventory. It reads a JSON file; it does
not contact or configure any machines.

| Lab | Build | Evidence to keep |
| --- | --- | --- |
| 1. Run locally | Your first validation rule | Valid and invalid inputs with their exit codes |
| 2. Lint | Automated code checks | A lint failure and its fix |
| 3. Test | Behaviour checks | A failing test, then a passing test |
| 4. Automate | Your own GitHub-hosted workflow | A failed PR check, a fixed check and a downloadable artifact |
| 5. Host a runner | A supervised, isolated runner | The same checks running on a lab VM |

Work happens in your own fork and comes back as a pull request. The starter intentionally lacks name
validation, and there is no completed workflow to copy.
