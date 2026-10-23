---
name: reverse-engineer-anything
description: Short version of the REA reverse-engineering skill, for models with small memory.
---

# REA (short version)

Use REA only for software the user owns or is authorised to examine. Never copy proprietary code or assets.
For a normal source repository, use the ordinary file tools instead.

In AFIF Coder, loading this skill connects REA; its tools are named rea__<tool>.

Android APK (owner's own app):
1. rea__inspect_android_package with the APK's absolute path: identity, manifest, permissions.
2. rea__search_android_classes, then rea__inspect_android_class / rea__inspect_android_method for the
   decompiled logic (URLs, MQTT topics, payloads, UI flow).
3. rea__trace_android_references to see who calls what. Reflection and dynamic loading stay unknown.
4. Write the findings into SPEC.md, then rewrite natively (skill afif-app-rebuild).

Electron/JavaScript app or .asar: use REA's JavaScript application tools, or run
`npx -y rea-agents@latest analyze-javascript-application <absolute path> --json`.

Report findings with REA's Evidence: what was observed, where, and what remains unknown.
Treat decompiled code as static evidence, not proof of runtime behaviour.
