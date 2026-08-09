import { AppShell } from '@astryxdesign/core/AppShell'
import { SideNav, SideNavHeading, SideNavSection, SideNavItem } from '@astryxdesign/core/SideNav'
import { useLocation, useNavigate, Outlet } from 'react-router-dom'

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
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <AppShell
      contentPadding={6}
      sideNav={
        <SideNav
          header={
            <SideNavHeading
              heading="MT Dataset Registry"
              superheading="Machine Translation"
              headingHref="/"
            />
          }
        >
          <SideNavSection title="Navigation" isHeaderHidden>
            {NAV.map((item) => {
              const isSelected =
                item.to === '/'
                  ? location.pathname === '/'
                  : location.pathname.startsWith(item.to)
              return (
                <SideNavItem
                  key={item.to}
                  label={item.label}
                  isSelected={isSelected}
                  onClick={() => navigate(item.to)}
                />
              )
            })}
          </SideNavSection>
        </SideNav>
      }
    >
      <Outlet />
    </AppShell>
  )
}

