import { useState } from 'react';
import { Palette, Settings, HelpCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useAppStore } from '@/store';
import { SettingsModal } from './SettingsModal';

export function TopNav() {
  const { theme, toggleTheme } = useAppStore();
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <header className="h-12 border-b border-border bg-card flex items-center justify-between px-4">
      <div className="flex items-center gap-2">
        <span className="font-bold text-lg tracking-tight text-primary">PyATPG</span>
      </div>
      <div className="flex items-center gap-4 text-muted-foreground">
        <button
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          className="hover:text-foreground transition-colors"
        >
          <Palette className="w-4 h-4" />
        </button>
        <button
          onClick={() => setSettingsOpen(true)}
          title="Settings"
          className="hover:text-foreground transition-colors"
        >
          <Settings className="w-4 h-4" />
        </button>
        <Link to="/help" title="Help & Documentation" className="hover:text-foreground transition-colors">
          <HelpCircle className="w-4 h-4" />
        </Link>
      </div>
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </header>
  );
}
