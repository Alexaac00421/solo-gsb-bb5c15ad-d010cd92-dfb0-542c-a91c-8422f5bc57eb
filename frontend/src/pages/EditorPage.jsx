import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { api, shareUrl } from '../api.js';
import PdfViewer from '../components/PdfViewer.jsx';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

let boxSeq = 1;

export default function EditorPage() {
  const { id } = useParams();
  const [doc, setDoc] = useState(null);
  const [pdfDoc, setPdfDoc] = useState(null);
  const [boxes, setBoxes] = useState([]);
  const [scale, setScale] = useState(1.2);
  const [viewRotation, setViewRotation] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const [published, setPublished] = useState(null);
  const previewUrlRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const meta = await api.getDocument(id);
        if (cancelled) return;
        setDoc(meta);
        setBoxes(meta.boxes.map((b) => ({ ...b, id: boxSeq++ })));
        const loaded = await pdfjs.getDocument(`/api/documents/${id}/file`).promise;
        if (cancelled) return;
        setPdfDoc(loaded);
      } catch (err) {
        if (!cancelled) setError(err.message || '加载失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(
    () => () => {
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    },
    []
  );

  const payloadBoxes = useMemo(
    () => boxes.map(({ page, x0, y0, x1, y1 }) => ({ page, x0, y0, x1, y1 })),
    [boxes]
  );

  const addBox = useCallback((box) => {
    setBoxes((prev) => [...prev, { ...box, id: boxSeq++ }]);
    setDirty(true);
    setPublished(null);
  }, []);

  const deleteBox = useCallback((boxId) => {
    setBoxes((prev) => prev.filter((b) => b.id !== boxId));
    setDirty(true);
    setPublished(null);
  }, []);

  const run = async (label, fn) => {
    setError('');
    setNotice('');
    setBusy(label);
    try {
      await fn();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy('');
    }
  };

  const onSave = () =>
    run('保存中…', async () => {
      await api.saveDraft(id, payloadBoxes);
      setDirty(false);
      setNotice(`草稿已保存（${payloadBoxes.length} 个遮盖框）`);
    });

  const onPreview = () =>
    run('生成预览…', async () => {
      const blob = await api.preview(id, payloadBoxes);
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      const url = URL.createObjectURL(blob);
      previewUrlRef.current = url;
      window.open(url, '_blank');
      setNotice('预览已在新标签页打开（即发布后的实际效果）');
    });

  const onPublish = () => {
    if (
      !window.confirm(
        `确认发布？将生成包含 ${payloadBoxes.length} 个遮盖框的脱敏 PDF，遮盖内容会从文件中彻底移除。`
      )
    )
      return;
    run('发布中…', async () => {
      const result = await api.publish(id, payloadBoxes);
      setDirty(false);
      setDoc(result);
      setPublished(result);
      setNotice('发布成功');
    });
  };

  if (error && !doc) {
    return (
      <div className="container">
        <div className="alert alert-error">{error}</div>
        <Link className="btn" to="/">
          返回列表
        </Link>
      </div>
    );
  }
  if (!doc) return <div className="page-center">加载中…</div>;

  return (
    <div className="editor">
      <div className="editor-toolbar">
        <Link className="btn btn-ghost" to="/">
          ← 返回
        </Link>
        <span className="editor-title" title={doc.filename}>
          {doc.filename}
        </span>
        <span className="muted">
          {doc.page_count} 页 · {boxes.length} 个遮盖框{dirty ? ' · 有未保存修改' : ''}
        </span>
        <div className="toolbar-group">
          <button className="btn btn-small" onClick={() => setScale((s) => Math.max(0.4, +(s - 0.2).toFixed(2)))}>
            缩小
          </button>
          <span className="muted">{Math.round(scale * 100)}%</span>
          <button className="btn btn-small" onClick={() => setScale((s) => Math.min(4, +(s + 0.2).toFixed(2)))}>
            放大
          </button>
          <button className="btn btn-small" onClick={() => setViewRotation((r) => (r + 90) % 360)}>
            旋转视图 {viewRotation}°
          </button>
        </div>
        <div className="toolbar-group">
          <button className="btn btn-small" disabled={!!busy} onClick={onSave}>
            保存草稿
          </button>
          <button className="btn btn-small" disabled={!!busy} onClick={onPreview}>
            预览脱敏结果
          </button>
          <button className="btn btn-primary btn-small" disabled={!!busy} onClick={onPublish}>
            发布
          </button>
        </div>
      </div>

      {busy && <div className="alert alert-info">{busy}</div>}
      {error && <div className="alert alert-error">{error}</div>}
      {notice && <div className="alert alert-ok">{notice}</div>}
      {(published || doc.share_token) && (
        <div className="alert alert-ok">
          已发布，分享链接（仅可下载脱敏版）：
          <a href={shareUrl(published ? published.share_token : doc.share_token)} target="_blank" rel="noreferrer">
            {shareUrl(published ? published.share_token : doc.share_token)}
          </a>
        </div>
      )}

      <div className="hint">
        在页面上按住鼠标拖出遮盖框；点击已有遮盖框可删除。缩放或旋转视图不影响选区与原页的对应关系。
      </div>

      {pdfDoc ? (
        <PdfViewer
          pdfDoc={pdfDoc}
          numPages={doc.page_count}
          scale={scale}
          viewRotation={viewRotation}
          boxes={boxes}
          onAddBox={addBox}
          onDeleteBox={deleteBox}
        />
      ) : (
        !error && <div className="page-center">正在渲染 PDF…</div>
      )}
    </div>
  );
}
