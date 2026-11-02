# 公开流程配图

本目录三个SVG均为项目原创流程示意，配套PNG位于`../images/`。不包含真实行情、私人会话、Key、业务策略或验收截图；标注“流程示意”不表示某一镜像已发布。历史界面截图仍单独标记其历史属性。

| SVG源码 | README使用的图片 |
| --- | --- |
| `daily-review-workflow.svg` | `../images/daily-review-workflow.png` |
| `strategy-evidence-flow.svg` | `../images/strategy-evidence-flow.png` |
| `ai-research-flow.svg` | `../images/ai-research-flow.png` |

PNG由SVG原生渲染，不依靠模型生成文字。文档生成工具为[resvg-js](https://github.com/thx/resvg-js)，临时使用`@resvg/resvg-js@2.6.2`；该第三方工具采用[MPL-2.0](https://github.com/thx/resvg-js/blob/main/LICENSE)，没有加入应用的前后端运行依赖，没有修改或嵌入其实现代码。字体由生成机器的系统提供，PNG不附带字体文件，跨平台像素可能因字体不同而变化。

在仓库根目录执行：

```bash
diagram_tools_dir=$(mktemp -d)
npm install --prefix "$diagram_tools_dir" --no-save --package-lock=false \
  --ignore-scripts --no-audit --no-fund @resvg/resvg-js@2.6.2
SHIXU_DOC_RENDER_MODULE="$diagram_tools_dir/node_modules/@resvg/resvg-js" \
  node scripts/render_public_diagrams.mjs
```

生成后逐张查看文字、连线及裁切，使用`scripts/package_release.py`的公开PNG校验核对文件格式与元信息；确认该临时目录没有进程引用后清理它。不要把安装目录、源行情或屏幕验收截图一起提交。
