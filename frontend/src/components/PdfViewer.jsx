import { useEffect, useRef, useState } from 'react';

/**
 * 单页渲染 + 框选层。
 *
 * 坐标约定：boxes 使用 PDF 默认用户空间（未旋转、y 轴向上、单位 pt）。
 * 绘制与回显都通过当前 viewport 双向转换（convertToPdfPoint /
 * convertToViewportPoint），因此无论视图缩放还是旋转，选区始终对应原页位置。
 */
function PageView({ pdfDoc, pageIndex, scale, viewRotation, boxes, onAddBox, onDeleteBox }) {
  const containerRef = useRef(null);
  const canvasRef = useRef(null);
  const overlayRef = useRef(null);
  const [viewport, setViewport] = useState(null);
  const [visible, setVisible] = useState(false);
  const [drag, setDrag] = useState(null);
  const dragRef = useRef(null);

  // 进入视口附近才开始渲染，避免长文档一次性渲染全部页面
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: '600px' }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // 计算 viewport（缩放/旋转变化时更新）
  useEffect(() => {
    let cancelled = false;
    pdfDoc.getPage(pageIndex + 1).then((page) => {
      if (cancelled) return;
      const rotation = (page.rotate + viewRotation + 360) % 360;
      setViewport(page.getViewport({ scale, rotation }));
    });
    return () => {
      cancelled = true;
    };
  }, [pdfDoc, pageIndex, scale, viewRotation]);

  // 渲染页面位图
  useEffect(() => {
    if (!visible || !viewport) return;
    let cancelled = false;
    let renderTask = null;
    pdfDoc.getPage(pageIndex + 1).then((page) => {
      if (cancelled) return;
      const canvas = canvasRef.current;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      renderTask = page.render({
        canvasContext: canvas.getContext('2d'),
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
      });
      renderTask.promise.catch(() => {
        /* 渲染被取消属正常情况 */
      });
    });
    return () => {
      cancelled = true;
      if (renderTask) renderTask.cancel();
    };
  }, [pdfDoc, pageIndex, viewport, visible]);

  const localPoint = (e) => {
    const rect = overlayRef.current.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  };

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    overlayRef.current.setPointerCapture(e.pointerId);
    const [x, y] = localPoint(e);
    dragRef.current = { x0: x, y0: y, x1: x, y1: y };
    setDrag(dragRef.current);
  };

  const onPointerMove = (e) => {
    if (!dragRef.current) return;
    const [x, y] = localPoint(e);
    dragRef.current = { ...dragRef.current, x1: x, y1: y };
    setDrag(dragRef.current);
  };

  const onPointerUp = () => {
    const d = dragRef.current;
    dragRef.current = null;
    setDrag(null);
    if (!d || !viewport) return;
    const w = Math.abs(d.x1 - d.x0);
    const h = Math.abs(d.y1 - d.y0);
    if (w < 4 || h < 4) return; // 误触忽略
    // 视图像素 → PDF 用户空间（自动处理缩放与旋转）
    const [ax, ay] = viewport.convertToPdfPoint(d.x0, d.y0);
    const [bx, by] = viewport.convertToPdfPoint(d.x1, d.y1);
    onAddBox({
      page: pageIndex,
      x0: Math.min(ax, bx),
      y0: Math.min(ay, by),
      x1: Math.max(ax, bx),
      y1: Math.max(ay, by),
    });
  };

  const width = viewport ? viewport.width : 600;
  const height = viewport ? viewport.height : 800;

  return (
    <div className="page-wrap" ref={containerRef}>
      <div className="page-stage" style={{ width, height }}>
        <canvas ref={canvasRef} />
        <div
          ref={overlayRef}
          className="overlay"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        >
          {viewport &&
            boxes.map((box) => {
              // PDF 用户空间 → 视图像素
              const [ax, ay] = viewport.convertToViewportPoint(box.x0, box.y0);
              const [bx, by] = viewport.convertToViewportPoint(box.x1, box.y1);
              const left = Math.min(ax, bx);
              const top = Math.min(ay, by);
              return (
                <div
                  key={box.id}
                  className="redact-box"
                  style={{ left, top, width: Math.abs(ax - bx), height: Math.abs(ay - by) }}
                  title="点击删除该遮盖框"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteBox(box.id);
                  }}
                />
              );
            })}
          {drag && (
            <div
              className="redact-box redact-box-draft"
              style={{
                left: Math.min(drag.x0, drag.x1),
                top: Math.min(drag.y0, drag.y1),
                width: Math.abs(drag.x1 - drag.x0),
                height: Math.abs(drag.y1 - drag.y0),
              }}
            />
          )}
        </div>
      </div>
      <div className="page-label">第 {pageIndex + 1} 页</div>
    </div>
  );
}

export default function PdfViewer({ pdfDoc, numPages, scale, viewRotation, boxes, onAddBox, onDeleteBox }) {
  const pages = Array.from({ length: numPages }, (_, i) => i);
  return (
    <div className="pdf-viewer">
      {pages.map((pageIndex) => (
        <PageView
          key={pageIndex}
          pdfDoc={pdfDoc}
          pageIndex={pageIndex}
          scale={scale}
          viewRotation={viewRotation}
          boxes={boxes.filter((b) => b.page === pageIndex)}
          onAddBox={onAddBox}
          onDeleteBox={onDeleteBox}
        />
      ))}
    </div>
  );
}
