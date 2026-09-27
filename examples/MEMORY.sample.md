# MEMORY.sample.md — pointer-dictionary fixture
#
# This is a *sample*, not real memory. Two entries are meant to resolve and one is meant
# to dangle, so you can see the guard's three output levels:
#
#   MEM_GUARD_SKILLS=skills MEM_GUARD_MEMORY=examples/MEMORY.sample.md \
#     python skills/memory-pointer-system/scripts/mem_guard.py --check --verbose
#
# Expected on a clean checkout: skill:// and doc://README.md resolve (✅ N 正常);
# doc://examples/does-not-exist.md is reported 🔴 as 断链 (the point of the fixture);
# cfg:// entries report 🔴 until you have a matching $HERMES_HOME/.env.

§
pp = skill://pulse-path | 方法论层：链表五律/删除=重接链
mps = skill://memory-pointer-system | 实现层：指针写法+mem_guard 体检
readme = doc://README.md | 本仓定位与 Quickstart
gone = doc://examples/does-not-exist.md | 故意留下的悬空指针，演示断链检
svc = cfg://SAMPLE_SERVICE_KEY | 取值见 .env，勿写进记忆正文
