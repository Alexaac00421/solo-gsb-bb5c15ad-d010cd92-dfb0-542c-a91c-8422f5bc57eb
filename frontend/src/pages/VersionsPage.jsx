import { Fragment, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api, shareUrl } from '../api.js';

export default function VersionsPage() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [filename, setFilename] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [meta, versions] = await Promise.all([api.getDocument(id), api.listVersions(id)]);
      setFilename(meta.filename);
      setData(versions);
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    load();
  }, [id]);

  const toggleBoxes = async (versionNo) => {
    if (expanded === versionNo) {
      setExpanded(null);
      return;
    }
    setExpanded(versionNo);
    if (!detail || detail.version_no !== versionNo) {
      try {
        setDetail(await api.getVersion(id, versionNo));
      } catch (err) {
        setError(err.message);
      }
    }
  };

  const copy = async (text, msg) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice(msg + text);
    } catch {
      window.prompt(msg, text);
    }
  };

  const onPinShare = async (versionNo) => {
    setError('');
    setBusy(true);
    try {
      const res = await api.createVersionShare(id, versionNo);
      const url = shareUrl(res.share_token);
      await copy(
        url,
        `第 ${versionNo} 版独立分享链接（内容固定，重发不改变）：`
      );
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (error && !data) {
    return (
      <div className="container">
        <div className="alert alert-error">{error}</div>
        <Link className="btn" to="/">
          返回列表
        </Link>
      </div>
    );
  }
  if (!data) return <div className="page-center">加载中…</div>;

  return (
    <div className="container">
      <div className="toolbar">
        <h2 title={filename}>版本记录 · {filename}</h2>
        <Link className="btn" to={`/edit/${id}`}>
          ← 返回编辑
        </Link>
      </div>

      <p className="muted">
        每次成功发布都会生成不可改的递增版本，留存发布时间、遮盖选区快照与成品 PDF。文档主分享链接与「下载脱敏版」始终指向第{' '}
        <strong>{data.current_version_no ?? '-'}</strong> 版（最近一次成功发布）；历史版本可单独取得固定分享链接。
      </p>

      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}

      {data.versions.length === 0 ? (
        <div className="empty">该文档尚未发布，暂无版本。</div>
      ) : (
        <table className="doc-table">
          <thead>
            <tr>
              <th>版本</th>
              <th>发布时间</th>
              <th>遮盖框</th>
              <th>独立分享链接</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {data.versions.map((v) => (
              <Fragment key={v.version_no}>
                <tr>
                  <td>
                    第 {v.version_no} 版
                    {v.is_current && (
                      <span className="badge badge-ok" style={{ marginLeft: 8 }}>
                        当前版本
                      </span>
                    )}
                  </td>
                  <td>{v.published_at}</td>
                  <td>
                    <button className="btn btn-link btn-small" onClick={() => toggleBoxes(v.version_no)}>
                      {v.box_count} 个{expanded === v.version_no ? ' ▲' : ' ▼'}
                    </button>
                  </td>
                  <td>
                    {v.share_token ? (
                      <button
                        className="btn btn-small"
                        onClick={() =>
                          copy(
                            shareUrl(v.share_token),
                            `第 ${v.version_no} 版固定分享链接：`
                          )
                        }
                      >
                        复制链接
                      </button>
                    ) : (
                      <span className="muted">未签发</span>
                    )}
                  </td>
                  <td className="actions">
                    <a
                      className="btn btn-small"
                      href={`/api/documents/${id}/versions/${v.version_no}/file`}
                    >
                      下载该版
                    </a>
                    <button
                      className="btn btn-small"
                      disabled={busy}
                      onClick={() => onPinShare(v.version_no)}
                    >
                      {v.share_token ? '重新复制固定链接' : '取得固定分享链接'}
                    </button>
                  </td>
                </tr>
                {expanded === v.version_no && detail && detail.version_no === v.version_no && (
                  <tr>
                    <td colSpan={5}>
                      <div className="snapshot">
                        <div className="muted">遮盖选区快照（PDF 用户空间坐标，0 基页码）：</div>
                        <pre className="snapshot-pre">
                          {JSON.stringify(detail.boxes, null, 2)}
                        </pre>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
