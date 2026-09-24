"""本地预览/编辑 Web 服务。

v1 范围：
- 只读/可编辑「未冻结」的草稿 spec；不触发任何付费生成（不调用 StoryVideoRunner.run）。
- storyvideo 项目：可读可编辑（台词、camera_move/camera_combo、dur），直接写回源 JSON。
- 本地跑，不做鉴权/多用户，不部署。

复用的都是已有代码：storyvideo.spec.load_spec / storyvideo.preview.render_preview /
check_duration_budget / storyvideo.prompts 的编译函数、camera_library.json。
没有另起一套 spec 解析或提示词编译逻辑。
"""
