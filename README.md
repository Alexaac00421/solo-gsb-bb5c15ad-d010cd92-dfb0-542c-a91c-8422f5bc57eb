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
| 分享下载页 | http://localhost:8000/share/{token} |
| 健康检查 | http://localhost:8000/api/health |

## 目录结构

```
├── Dockerfile              # 多阶段：前端构建 + Python 运行时
├── docker-compose.yml      # 一键启动
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 入口、静态资源与 SPA 回退
│   │   ├── pdfcore.py      # PDF 校验与脱敏核心（坐标转换、内容移除、清理）
│   │   ├── routes/         # auth / documents / share 路由
│   │   ├── db.py           # SQLite 数据访问
│   │   └── ...
│   └── tests/              # pytest：脱敏正确性、越界/加密/损坏拒绝、权限隔离
└── frontend/
    └── src/
        ├── components/PdfViewer.jsx  # 页面渲染 + 框选层（缩放/旋转坐标映射）
        └── pages/                    # 登录、文档列表、编辑器、分享下载页
```
