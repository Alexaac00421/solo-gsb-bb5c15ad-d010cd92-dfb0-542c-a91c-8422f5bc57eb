import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { api } from './api.js';
import LoginPage from './pages/LoginPage.jsx';
import DocumentsPage from './pages/DocumentsPage.jsx';
import EditorPage from './pages/EditorPage.jsx';
import SharePage from './pages/SharePage.jsx';

const AuthContext = createContext(null);

export function useAuth() {
  return useContext(AuthContext);
}

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

function RequireAuth({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="page-center">加载中…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;
  return (
    <header className="app-header">
      <a className="brand" href="/">
        脱敏资料发布平台
      </a>
      <div className="header-right">
        <span className="username">{user.username}</span>
        <button
          className="btn btn-ghost"
          onClick={async () => {
            await logout();
            navigate('/login');
          }}
        >
          退出登录
        </button>
      </div>
    </header>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Header />
      <main>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <DocumentsPage />
              </RequireAuth>
            }
          />
          <Route
            path="/edit/:id"
            element={
              <RequireAuth>
                <EditorPage />
              </RequireAuth>
            }
          />
          <Route path="/share/:token" element={<SharePage />} />
          <Route path="/share/v/:vToken" element={<SharePage />} />
          <Route path="*" element={<div className="page-center">页面不存在</div>} />
        </Routes>
      </main>
    </AuthProvider>
  );
}
