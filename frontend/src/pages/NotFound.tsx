import { Link } from 'react-router-dom';
import { CircuitBoard } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <header className="flex items-center gap-2 border-b border-border px-8 py-5">
        <CircuitBoard className="h-6 w-6 text-primary" />
        <span className="text-lg font-semibold tracking-tight">PyATPG</span>
      </header>

      <div className="flex flex-1 items-center justify-center bg-muted/30 px-8 py-16">
        <div className="flex w-full max-w-4xl flex-col items-center gap-10 md:flex-row md:justify-between">
          <img
            src="/porygon.png"
            alt="A confused Porygon-Z"
            className="h-64 w-64 object-contain drop-shadow-xl md:h-72 md:w-72"
          />

          <div className="flex flex-col items-center text-center md:items-start md:text-left">
            <h1 className="text-4xl font-extrabold leading-tight tracking-tight sm:text-5xl">
              OOPS! PAGE
              <br />
              NOT FOUND.
            </h1>
            <p className="mt-4 max-w-sm text-muted-foreground">
              Looks like this signal traced to a dead net. The page you're
              looking for doesn't exist.
            </p>
            <Link
              to="/"
              className="mt-8 inline-flex items-center justify-center rounded-full bg-primary px-8 py-3 text-sm font-semibold text-primary-foreground shadow transition-colors hover:bg-primary/90"
            >
              BACK TO HOME
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
