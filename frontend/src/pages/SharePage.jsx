import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api.js';

export default function SharePage() {
  const { token } = useParams();
  const [info, setInfo] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.shareInfo(token).then(setInfo).catch((err) => setError(err.message));
  }, [token]);

  return (
    <div className="page-center">
      <div className="card share-card">
        <h1>脱敏资料下载</h1>
        {error && <div className="alert alert-error">{error}</div>}
        {info && (
          <>
            <p>
              文件：<strong>{info.filename}</strong>
            </p>
            {info.version_no != null && (
              <p className="muted">
                版本：第 {info.version_no} 版
                {info.pinned ? '（固定版本，后续重发不改变本链接内容）' : '（文档最新发布版）'}
              </p>
            )}
            <p className="muted">发布时间：{info.published_at}</p>
            <p className="muted">该链接仅提供对应版本的脱敏成品。</p>
            <a className="btn btn-primary" href={`/api/share/${token}`}>
              下载脱敏 PDF
            </a>
          </>
        )}
        {!info && !error && <p>加载中…</p>}
      </div>
    </div>
  );
}
