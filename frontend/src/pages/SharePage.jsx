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
            <p className="muted">发布时间：{info.published_at}</p>
            <p className="muted">该链接仅提供已发布的脱敏版本。</p>
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
