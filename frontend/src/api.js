async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers,
  });
  if (!res.ok) {
    let detail = `请求失败（HTTP ${res.status}）`;
    try {
      const data = await res.json();
      if (data && data.detail) detail = data.detail;
    } catch {
      /* 非 JSON 响应 */
    }
    throw new Error(detail);
  }
  return res;
}

const json = (res) => res.json();

export const api = {
  me: () => request('/api/auth/me').then(json),
  login: (username, password) =>
    request('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }).then(json),
  register: (username, password) =>
    request('/api/auth/register', { method: 'POST', body: JSON.stringify({ username, password }) }).then(json),
  logout: () => request('/api/auth/logout', { method: 'POST' }).then(json),

  listDocuments: () => request('/api/documents').then(json),
  uploadDocument: (file) => {
    const form = new FormData();
    form.append('file', file);
    return request('/api/documents', { method: 'POST', body: form }).then(json);
  },
  getDocument: (id) => request(`/api/documents/${id}`).then(json),
  deleteDocument: (id) => request(`/api/documents/${id}`, { method: 'DELETE' }).then(json),
  saveDraft: (id, boxes) =>
    request(`/api/documents/${id}/draft`, { method: 'PUT', body: JSON.stringify({ boxes }) }).then(json),
  preview: (id, boxes) =>
    request(`/api/documents/${id}/preview`, { method: 'POST', body: JSON.stringify({ boxes }) }).then((r) => r.blob()),
  publish: (id, boxes) =>
    request(`/api/documents/${id}/publish`, { method: 'POST', body: JSON.stringify({ boxes }) }).then(json),
  listVersions: (id) => request(`/api/documents/${id}/versions`).then(json),
  getVersion: (id, versionNo) =>
    request(`/api/documents/${id}/versions/${versionNo}`).then(json),
  createVersionShare: (id, versionNo) =>
    request(`/api/documents/${id}/versions/${versionNo}/share`, { method: 'POST' }).then(json),
  shareInfo: (token) => request(`/api/share/${token}/info`).then(json),
};

export function shareUrl(token) {
  return `${window.location.origin}/share/${token}`;
}
