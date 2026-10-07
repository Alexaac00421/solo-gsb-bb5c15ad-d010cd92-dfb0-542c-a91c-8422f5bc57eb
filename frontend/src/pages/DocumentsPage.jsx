import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, shareUrl } from '../api.js';

export default function DocumentsPage() {
  const [docs, setDocs] = useState([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef(null);

  const load = async () => {
    try {
      setDocs(await api.listDocuments());
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const onUpload = async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setError('');
    setNotice('');
    setUploading(true);
    try {
      await api.uploadDocument(file);
      setNotice(`「${file.name}」上传成功，可开始框选遮盖区域`);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  };

  const onDelete = async (doc) => {
    if (!window.confirm(`确定删除「${doc.filename}」？原件、草稿与已发布版本都会被移除。`)) return;
    try {
      await api.deleteDocument(doc.id);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const copyShare = async (doc) => {
    const url = shareUrl(doc.share_token);
    try {
      await navigator.clipboard.writeText(url);
      setNotice('分享链接已复制：' + url);
    } catch {
      window.prompt('复制分享链接：', url);
    }
  };

  return (
    <div className="container">
      <div className="toolbar">
        <h2>我的文档</h2>
        <button className="btn btn-primary" disabled={uploading} onClick={() => fileInput.current.click()}>
          {uploading ? '上传中…' : '上传 PDF'}
        </button>
        <input ref={fileInput} type="file" accept="application/pdf,.pdf" hidden onChange={onUpload} />
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}
      {docs.length === 0 ? (
        <div className="empty">暂无文档，请上传 PDF 开始。加密或损坏的文件会被拒绝。</div>
      ) : (
        <table className="doc-table">
          <thead>
            <tr>
              <th>文件名</th>
              <th>页数</th>
              <th>遮盖框</th>
              <th>状态</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((doc) => (
              <tr key={doc.id}>
                <td className="filename">{doc.filename}</td>
                <td>{doc.page_count}</td>
                <td>{doc.box_count}</td>
                <td>
                  {doc.status === 'published' ? (
                    <span className="badge badge-ok">已发布</span>
                  ) : (
                    <span className="badge">草稿</span>
                  )}
                </td>
                <td>{doc.updated_at}</td>
                <td className="actions">
                  <Link className="btn btn-small" to={`/edit/${doc.id}`}>
                    编辑遮盖
                  </Link>
                  <a className="btn btn-small" href={`/api/documents/${doc.id}/file`}>
                    下载原件
                  </a>
                  {doc.status === 'published' && (
                    <>
                      <a className="btn btn-small" href={`/api/documents/${doc.id}/published`}>
                        下载脱敏版
                      </a>
                      <button className="btn btn-small" onClick={() => copyShare(doc)}>
                        复制分享链接
                      </button>
                      <Link className="btn btn-small" to={`/documents/${doc.id}/versions`}>
                        历史版本
                      </Link>
                    </>
                  )}
                  <button className="btn btn-small btn-danger" onClick={() => onDelete(doc)}>
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
