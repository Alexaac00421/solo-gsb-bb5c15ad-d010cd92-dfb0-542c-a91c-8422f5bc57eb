# 脱敏资料发布平台

供机构在**本机**处理并发布脱敏 PDF 的 Web 应用：上传 PDF 后逐页框选遮盖区域，保存草稿、预览效果，确认后发布生成新的脱敏 PDF，并通过分享链接对外提供下载。

- 后端：Python · FastAPI · SQLite · PyMuPDF（PDF 处理全部在本机完成，不依赖任何外部服务）
- 前端：React · Vite · pdf.js
- 交付：单容器 Dockerfile + docker compose 一键启动

## 快速启动

```bash
docker compose up --build
```

启动后访问：**http://localhost:8000**

首次使用请注册账号（默认开放注册）。上传 PDF → 进入"编辑遮盖"框选区域 → 保存草稿 / 预览 → 发布 → 复制分享链接。

本地开发（可选）：

```bash
# 后端（端口 8000）
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# 前端（端口 5173，代理 /api 到 8000）
cd frontend
npm install
npm run dev

# 后端测试
cd backend && python -m pytest tests/
```

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 容器内监听端口（compose 已映射到本机 8000） |
| `DATA_DIR` | `/data`（容器）/ `./data`（本地） | SQLite 数据库与 PDF 文件存储目录 |
| `ALLOW_REGISTRATION` | `true` | 是否开放注册；首个账号注册不受此限制 |
| `MAX_UPLOAD_MB` | `100` | 单个 PDF 上传大小上限（MB） |
| `SESSION_TTL_HOURS` | `72` | 登录会话有效期（小时） |

数据（数据库、原件、已发布文件）持久化在 compose 的 `app_data` 卷中。

## 功能与保证

- **逐页框选**：在页面上拖拽画出遮盖框，点击已有框可删除；视图缩放、旋转后选区仍精确对应原页位置（选区以 PDF 用户空间坐标存储，与视图无关）。
- **草稿与预览**：草稿保存在服务端，可随时继续编辑；预览即时生成脱敏后的完整 PDF（新标签页打开），即发布后的真实效果。
- **版本化发布**：
  - 同一文档**每次成功发布都形成一个不可修改的递增版本**（v1、v2、…），版本留存发布时间、发布时的遮盖选区快照与脱敏成品 PDF；
  - 创建者可在编辑器下方的「历史版本」面板查看版本记录、回看任一版本的选区快照、下载任一版本；
  - **文档级分享链接与「下载脱敏版」始终指向最近一次成功发布的版本**（链接地址不变，内容跟随最新成功发布）；
  - 创建者可为**指定版本取得独立的固定分享链接**（`/share/v/{token}`），持链者只能下载该版脱敏成品，之后无论怎样重新发布，该链接内容都不会改变；
  - 并发发布在服务端串行分配版本号，必然得到互不相同的版本号；当前版本只指向最后成功提交的版本；
  - 选区校验、PDF 导出或文件写入失败都不会留下可见的半成品版本，也不改变当前版本与既有链接（原有草稿失败处理规则保持：校验失败不动草稿，导出失败保留已保存草稿）；
  - 删除文档后所有分享链接（文档级 + 版本固定链接）立即失效，全部版本文件一并清理；
  - 非创建者不能查看版本记录、下载版本或取得链接；版本文件缺失时一律报错，绝不静默回退到其他版本。
- **发布输出**：
  - 遮盖区域内的文字、图像、矢量图形从内容流中**真正移除**（非视觉遮挡），区域外原本可搜索的文字保持可搜索；
  - 原页尺寸、旋转与页面顺序保持不变；
  - 输出不携带原文件的注释、链接、附件、书签与元数据；
  - 写出采用临时文件 + 原子替换，导出失败不会破坏原件、草稿或已发布版本。
- **输入校验**：越界选区、加密（含空密码加密）或损坏的 PDF 一律拒绝，且不会覆盖已有草稿。
- **权限**：只有文档创建者（登录后）可下载原件与执行编辑/发布；持分享链接的访问者只能下载已发布的脱敏版，无需登录。

## 访问地址

| 入口 | 地址 |
| --- | --- |
| 应用首页（登录/文档管理） | http://localhost:8000/ |
| 分享下载页（文档级，始终最新版） | http://localhost:8000/share/{token} |
| 版本固定分享下载页（仅该版本） | http://localhost:8000/share/v/{versionToken} |
| 健康检查 | http://localhost:8000/api/health |

## 历史版本的页面用法

1. 打开「编辑遮盖」页，正常框选后点「发布」；每次成功发布都会在页面下方「历史版本」面板新增一条 `vN` 记录。
2. 在历史版本面板可：
   - **查看遮盖选区**：只读叠加显示该版本发布时的选区快照（蓝框），可缩放/旋转查看，不影响当前草稿；
   - **下载该版**：下载该版本的脱敏成品 PDF；
   - **取得固定分享链接**：为该版本生成永久固定的独立链接，按钮随后变为「复制固定链接」。
3. 文档列表与编辑器顶部的链接是**文档级链接**，永远指向最近一次成功发布的版本；需要把某一版长期对外提供时，请使用该版本自己的固定链接。

## HTTP API（版本相关）

除页面操作外，历史版本能力也全部可经 HTTP API 调用。除两类分享下载外，下列接口均需要创建者登录态（会话 Cookie）。现有上传、草稿、预览、发布接口的请求体与返回字段保持兼容（发布/文档返回仅**新增** `current_version` 字段）。

```bash
# 发布（成功后返回 current_version；文档级 share_token 保持不变）
curl -s -c cookies.txt -X POST -H 'Content-Type: application/json' \
  -d '{"boxes":[{"page":0,"x0":60,"y0":670,"x1":320,"y1":720}]}' \
  http://localhost:8000/api/documents/$DOC_ID/publish
# -> {"id":"...","status":"published","share_token":"<文档级token>","current_version":1,...}

# 查看版本记录（新版本在前；is_current 标记当前版本）
curl -s -b cookies.txt http://localhost:8000/api/documents/$DOC_ID/versions
# -> [{"version":2,"published_at":"2026-10-07 08:12:34","is_current":true},
#     {"version":1,"published_at":"2026-10-07 08:12:32","is_current":false}]

# 查看单个版本（含发布时保存的遮盖选区快照 boxes）
curl -s -b cookies.txt http://localhost:8000/api/documents/$DOC_ID/versions/1

# 创建者下载任一版本脱敏成品
curl -s -b cookies.txt -o v1.pdf \
  http://localhost:8000/api/documents/$DOC_ID/versions/1/download

# 为指定版本取得固定分享链接（重复调用返回同一链接）
curl -s -b cookies.txt -X POST \
  http://localhost:8000/api/documents/$DOC_ID/versions/1/share
# -> {"share_token":"<版本token>","share_url":"/api/share/v/<版本token>","version":1,...}
```

无需登录的分享下载：

```bash
# 文档级链接：始终解析为最近一次成功发布版本（/api/share/{token}/info 可查版本号）
curl -s -o latest.pdf http://localhost:8000/api/share/$DOC_TOKEN

# 版本固定链接：永远只返回创建链接时指定的那一版，后续重发不改变其内容
curl -s -o v1.pdf  http://localhost:8000/api/share/v/$VERSION_TOKEN
curl -s          http://localhost:8000/api/share/v/$VERSION_TOKEN/info
# -> {"filename":"doc.pdf","published_at":"2026-10-07 08:12:32","version":1}
```

版本文件缺失或版本记录异常时，下载接口返回 500 并说明未回退；链接/版本不存在返回 404；非创建者访问版本接口返回 404。

## 目录结构

```
├── Dockerfile              # 多阶段：前端构建 + Python 运行时
├── docker-compose.yml      # 一键启动
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 入口、静态资源与 SPA 回退
│   │   ├── pdfcore.py      # PDF 校验与脱敏核心（坐标转换、内容移除、清理）
│   │   ├── routes/         # auth / documents（含历史版本）/ share（文档级+版本固定链接）路由
│   │   ├── db.py           # SQLite 数据访问（versions / version_shares 与旧库迁移）
│   │   └── ...
│   └── tests/              # pytest：脱敏正确性、越界/加密/损坏拒绝、权限隔离、版本化与并发发布
└── frontend/
    └── src/
        ├── components/PdfViewer.jsx      # 页面渲染 + 框选层（缩放/旋转坐标映射，支持只读快照）
        ├── components/VersionHistory.jsx # 历史版本面板（记录、选区快照、下载、固定链接）
        └── pages/                        # 登录、文档列表、编辑器、分享下载页（含版本固定链接页）
```
