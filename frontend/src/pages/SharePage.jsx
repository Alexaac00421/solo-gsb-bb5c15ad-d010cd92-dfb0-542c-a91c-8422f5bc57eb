import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api.js';

export default function SharePage() {
  const { token, vToken } = useParams();
  const pinned = Boolean(vToken);
  const actualToken = vToken || token;
  const [info, setInfo] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setInfo(null);
    setError('');
    const fetcher = pinned ? api.versionShareInfo : api.shareInfo;
    fetcher(actualToken)
      .then(setInfo)
      .catch((err) => setError(err.message));
  }, [actualToken, pinned]);

  const downloadUrl = pinned ? `/api/share/v/${actualToken}` : `/api/share/${actualToken}`;

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
            <p className="muted">
              {pinned ? '固定版本' : '最新发布版本'}：v{info.version}
            </p>
            <p className="muted">发布时间：{info.published_at}</p>
            <p className="muted">
              {pinned
                ? '该链接永久固定在上述版本，只提供该版本的脱敏成品。'
                : '该链接始终指向最近一次成功发布的版本，仅提供脱敏成品。'}
            </p>
            <a className="btn btn-primary" href={downloadUrl}>
              下载脱敏 PDF
            </a>
          </>
        )}
        {!info && !error && <p>加载中…</p>}
      </div>
    </div>
  );
}
