import { useEffect, useState } from 'react';
import { api, versionShareApiUrl } from '../api.js';

/**
 * 历史版本面板（仅创建者可见）：
 * - 列出每次成功发布的不可改版本（版本号、发布时间、是否当前版）
 * - 查看任一版本发布时保存的遮盖选区快照（只读叠加层）
 * - 下载任一版本脱敏成品
 * - 为指定版本取得独立分享链接（永久固定该版本）
 */
export default function VersionHistory({ docId, refreshKey, onViewSnapshot, snapshotVersion, onCloseSnapshot }) {
  const [versions, setVersions] = useState(null);
  const [error, setError] = useState('');
  const [busyVersion, setBusyVersion] = useState(null);
  const [notice, setNotice] = useState('');

  const load = () => {
    api
      .listVersions(docId)
      .then(setVersions)
      .catch((err) => setError(err.message));
  };

  useEffect(load, [docId, refreshKey]);

  const copy = async (text, label) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice(`${label}已复制：${text}`);
    } catch {
      window.prompt('复制链接：', text);
    }
  };

  const onMakeLink = async (version) => {
    setError('');
    setNotice('');
    setBusyVersion(version);
    try {
      const res = await api.createVersionShare(docId, version);
      setVersions((prev) =>
        prev.map((v) =>
          v.version === version ? { ...v, share_token: res.share_token, share_url: res.share_url } : v
        )
      );
      await copy(versionShareApiUrl(res.share_token), `v${version} 独立分享链接`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyVersion(null);
    }
  };

  if (!versions) return <div className="hint">{error || '正在加载版本记录…'}</div>;
  if (versions.length === 0) return null;

  return (
    <section className="versions">
      <h3 className="versions-title">历史版本（{versions.length}）</h3>
      <p className="muted">
        每次成功发布生成不可修改的新版本；文档级分享链接始终指向最新成功版本，也可为指定版本取得固定链接。
      </p>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}
      <table className="version-table">
        <thead>
          <tr>
            <th>版本</th>
            <th>发布时间</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.version} className={snapshotVersion === v.version ? 'row-active' : ''}>
              <td>v{v.version}</td>
              <td>{v.published_at}</td>
              <td>
                {v.is_current ? <span className="badge badge-ok">当前版本</span> : <span className="badge">历史版本</span>}
              </td>
              <td className="actions">
                <button
                  className="btn btn-small"
                  onClick={() => (snapshotVersion === v.version ? onCloseSnapshot() : onViewSnapshot(v.version))}
                >
                  {snapshotVersion === v.version ? '退出选区快照' : '查看遮盖选区'}
                </button>
                <a className="btn btn-small" href={api.versionDownloadUrl(docId, v.version)}>
                  下载该版
                </a>
                {v.share_token ? (
                  <button
                    className="btn btn-small"
                    onClick={() => copy(versionShareApiUrl(v.share_token), `v${v.version} 固定分享链接`)}
                  >
                    复制固定链接
                  </button>
                ) : (
                  <button
                    className="btn btn-small"
                    disabled={busyVersion === v.version}
                    onClick={() => onMakeLink(v.version)}
                  >
                    {busyVersion === v.version ? '创建中…' : '取得固定分享链接'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
