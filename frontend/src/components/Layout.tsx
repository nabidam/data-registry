import { NavLink, Outlet } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Dashboard' },
  { to: '/samples', label: 'Samples' },
  { to: '/batches', label: 'Batches' },
  { to: '/sources', label: 'Sources' },
  { to: '/datasets', label: 'Datasets' },
  { to: '/snapshots', label: 'Snapshots' },
  { to: '/splits', label: 'Splits' },
  { to: '/evaluation-sets', label: 'Evaluation Sets' },
  { to: '/annotations', label: 'Annotations' },
  { to: '/experiments', label: 'Experiments' },
  { to: '/models', label: 'Models' },
  { to: '/statistics', label: 'Statistics' },
  { to: '/imports', label: 'Imports' },
  { to: '/exports', label: 'Exports' },
  { to: '/settings', label: 'Settings' },
]

export default function Layout() {
  return (
    <div className="flex min-h-screen">
      <aside className="w-56 shrink-0 border-r border-slate-200 bg-white">
        <div className="px-4 py-4 text-sm font-semibold">MT Dataset Registry</div>
        <nav className="flex flex-col gap-0.5 px-2 pb-4">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `rounded-md px-3 py-1.5 text-sm ${
                  isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-100'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  )
}
