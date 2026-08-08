import { Link, useLocation } from 'react-router-dom';
import { 
  LayoutDashboard, 
  Cpu, 
  FileBox, 
  BarChart2, 
  Zap, 
  ListOrdered, 
  Rocket, 
  BarChart, 
  FileText 
} from 'lucide-react';
import { cn } from '@/utils/utils';

const navItems = [
  { icon: LayoutDashboard, label: 'Dashboard', path: '/' },
  { icon: Cpu, label: 'Circuits', path: '/circuits' },
  { icon: FileBox, label: 'Trojan Dataset', path: '/trojan' },
  { icon: BarChart2, label: 'Feature Analysis', path: '/features' },
  { icon: Zap, label: 'ATPG', path: '/atpg' },
  { icon: ListOrdered, label: 'Ranking', path: '/ranking' },
  { icon: Rocket, label: 'Benchmark', path: '/benchmark' },
  { icon: BarChart, label: 'Results', path: '/results' },
  { icon: FileText, label: 'Reports', path: '/reports' },
];

export function Sidebar() {
  const location = useLocation();

  return (
    <aside className="w-64 bg-card border-r border-border h-full flex flex-col">
      <div className="p-4 border-b border-border flex items-center gap-3">
        <img src="/pyatpg.png" alt="PyATPG" className="w-10 h-10 rounded-full shrink-0" />
        <div>
          <h1 className="text-lg font-bold text-primary leading-tight">PyATPG</h1>
          <p className="text-xs text-muted-foreground leading-tight">Trojan Localization</p>
        </div>
      </div>
      <nav className="flex-1 py-4">
        <ul className="space-y-1 px-2">
          {navItems.map((item) => {
            const isActive = location.pathname === item.path;
            const Icon = item.icon;
            return (
              <li key={item.path}>
                <Link
                  to={item.path}
                  className={cn(
                    "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                    isActive 
                      ? "bg-secondary text-secondary-foreground font-medium" 
                      : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground"
                  )}
                >
                  <Icon className="w-4 h-4" />
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
    </aside>
  );
}
