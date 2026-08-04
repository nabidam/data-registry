import type { ReactNode } from 'react'

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500">{subtitle}</p>}
      </div>
      <div className="flex gap-2">{actions}</div>
    </div>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-4 ${className}`}>{children}</div>
  )
}

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Card>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </Card>
  )
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger'
}

export function Button({ variant = 'primary', className = '', ...props }: ButtonProps) {
  const styles = {
    primary: 'bg-slate-900 text-white hover:bg-slate-700',
    ghost: 'border border-slate-300 bg-white hover:bg-slate-100',
    danger: 'border border-red-300 text-red-700 hover:bg-red-50',
  }[variant]
  return (
    <button
      className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50 ${styles} ${className}`}
      {...props}
    />
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-slate-600">{label}</span>
      {children}
    </label>
  )
}

const inputClass =
  'w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-slate-500'

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputClass} ${props.className ?? ''}`} />
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputClass} ${props.className ?? ''}`} />
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`${inputClass} ${props.className ?? ''}`} />
}

export function Badge({ children }: { children: ReactNode }) {
  const tone =
    children === 'ready'
      ? 'bg-emerald-100 text-emerald-800'
      : children === 'failed'
        ? 'bg-red-100 text-red-800'
        : 'bg-slate-100 text-slate-700'
  return <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tone}`}>{children}</span>
}

export function Empty({ message }: { message: string }) {
  return <div className="py-10 text-center text-sm text-slate-500">{message}</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <div className="mb-4 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
      {String((error as Error).message ?? error)}
    </div>
  )
}
