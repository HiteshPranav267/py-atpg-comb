import { Outlet } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopNav } from './TopNav';
import { StatusBar } from './StatusBar';
import { useEffect } from 'react';
import { api } from '@/services/api';
import { useAppStore } from '@/store';

export function AppLayout() {
  const setBackendStatus = useAppStore(state => state.setBackendStatus);
  const theme = useAppStore(state => state.theme);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark');
  }, [theme]);

  useEffect(() => {
    // Basic health check to set status
    const checkHealth = async () => {
      try {
        await api.healthCheck();
        setBackendStatus('CONNECTED');
      } catch (error) {
        setBackendStatus('DISCONNECTED');
      }
    };
    checkHealth();
    const interval = setInterval(checkHealth, 10000);
    return () => clearInterval(interval);
  }, [setBackendStatus]);

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-background text-foreground">
      <TopNav />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-auto bg-background">
          <Outlet />
        </main>
      </div>
      <StatusBar />
    </div>
  );
}
