import { Palette, Settings, HelpCircle } from 'lucide-react';

export function TopNav() {
  return (
    <header className="h-12 border-b border-border bg-card flex items-center justify-between px-4">
      <div className="flex items-center gap-2">
        <span className="font-bold text-lg tracking-tight text-primary">PyATPG</span>
      </div>
      <div className="flex items-center gap-4 text-muted-foreground">
        <button className="hover:text-foreground transition-colors"><Palette className="w-4 h-4" /></button>
        <button className="hover:text-foreground transition-colors"><Settings className="w-4 h-4" /></button>
        <button className="hover:text-foreground transition-colors"><HelpCircle className="w-4 h-4" /></button>
      </div>
    </header>
  );
}
