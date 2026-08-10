import React, { type ReactNode, useState } from 'react'
import { Card as AstryxCard } from '@astryxdesign/core/Card'
import { Heading, Text } from '@astryxdesign/core/Text'
import { Button as AstryxButton } from '@astryxdesign/core/Button'
import { Field as AstryxField } from '@astryxdesign/core/Field'
import { TextInput as AstryxTextInput } from '@astryxdesign/core/TextInput'
import { Selector as AstryxSelector } from '@astryxdesign/core/Selector'
import { TextArea as AstryxTextArea } from '@astryxdesign/core/TextArea'
import { CheckboxInput as AstryxCheckboxInput } from '@astryxdesign/core/CheckboxInput'
import { FileInput as AstryxFileInput } from '@astryxdesign/core/FileInput'
import { RadioList as AstryxRadioList, RadioListItem as AstryxRadioListItem } from '@astryxdesign/core/RadioList'
import { Badge as AstryxBadge } from '@astryxdesign/core/Badge'
import { EmptyState as AstryxEmptyState } from '@astryxdesign/core/EmptyState'
import { Banner as AstryxBanner } from '@astryxdesign/core/Banner'
import { HStack, VStack } from '@astryxdesign/core/Stack'

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
    <VStack gap={2} style={{ marginBottom: '1.5rem' }}>
      <HStack vAlign="center" hAlign="between">
        <VStack gap={1}>
          <Heading level={1}>{title}</Heading>
          {subtitle && (
            <Text type="supporting" color="secondary">
              {subtitle}
            </Text>
          )}
        </VStack>
        {actions && <HStack gap={2} vAlign="center">{actions}</HStack>}
      </HStack>
    </VStack>
  )
}

export function Card({
  children,
  className = '',
  padding = 4,
  style,
}: {
  children: ReactNode
  className?: string
  padding?: any
  style?: React.CSSProperties
}) {
  return (
    <AstryxCard className={className} width="100%" padding={padding} style={style as any}>
      {children}
    </AstryxCard>
  )
}

export function Stat({
  label,
  value,
  loading,
  variant = 'normal',
}: {
  label: string
  value: ReactNode
  loading?: boolean
  variant?: 'normal' | 'warning' | 'danger'
}) {
  const isDanger = variant === 'danger'
  const isWarning = variant === 'warning'

  return (
    <AstryxCard
      width="100%"
      padding={4}
      style={{
        borderColor: isDanger
          ? 'rgba(239, 68, 68, 0.4)'
          : isWarning
          ? 'rgba(245, 158, 11, 0.4)'
          : undefined,
        backgroundColor: isDanger
          ? 'rgba(239, 68, 68, 0.05)'
          : isWarning
          ? 'rgba(245, 158, 11, 0.05)'
          : undefined,
      }}
    >
      <VStack gap={1}>
        <Text
          type="supporting"
          color="secondary"
          weight="medium"
          style={{ color: isDanger ? '#ef4444' : isWarning ? '#f59e0b' : undefined }}
        >
          {label}
        </Text>
        <Heading level={3}>
          {loading ? (
            <span className="inline-block w-20 h-7 bg-slate-700/50 animate-pulse rounded" />
          ) : (
            value
          )}
        </Heading>
      </VStack>
    </AstryxCard>
  )
}

/**
 * Flattens renderable children to plain text. Astryx components take a `label`
 * string for accessibility, while call sites write JSX children — including
 * interpolated fragments like `Attempt {n}`, which `String()` would mangle.
 */
function toText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(toText).join('')
  if (React.isValidElement(node)) return toText((node.props as { children?: ReactNode }).children)
  return ''
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger'
  label?: string
}

export function Button({ variant = 'primary', className = '', label, children, ...props }: ButtonProps) {
  const astryxVariant = variant === 'danger' ? 'destructive' : variant === 'ghost' ? 'ghost' : 'primary'
  const buttonLabel = label || (typeof children === 'string' ? children : toText(children))

  // AstryxButton defaults `type` to "button"; native <button> defaults to "submit".
  // Call sites rely on the native default (they mark non-submitting buttons with
  // type="button" explicitly), so restore it or in-form buttons never submit.
  const buttonType = props.type ?? 'submit'

  return (
    <AstryxButton
      label={buttonLabel || 'Action'}
      variant={astryxVariant}
      isDisabled={props.disabled}
      type={buttonType as any}
      onClick={props.onClick as any}
      className={className}
    >
      {/* Plain-text children are covered by `label`; richer children (icons,
          fragments) must still render, with `label` serving accessibility. */}
      {typeof children === 'string' || children == null ? null : children}
    </AstryxButton>
  )
}

/**
 * Controls that render their own Astryx `Field` (label + control, wired via an
 * internally generated id). `Field` below hands its label to them instead of
 * wrapping them, because the wrapper's `htmlFor` can never reach the control:
 * Astryx inputs override any incoming `id` with their own `useId()` value.
 */
type LabelledControl = { acceptsFieldLabel?: boolean }

function acceptsFieldLabel(node: ReactNode): node is React.ReactElement<{ label?: string }> {
  return (
    React.isValidElement(node) &&
    (node.type as LabelledControl)?.acceptsFieldLabel === true &&
    !(node.props as { label?: string }).label
  )
}

export function Field({ label, children, inputID }: { label: string; children: ReactNode; inputID?: string }) {
  // Astryx controls own their label markup, so pass the label down rather than
  // rendering a second, disconnected <label> around them.
  if (acceptsFieldLabel(children)) {
    return React.cloneElement(children, { label })
  }

  const id = inputID || label.toLowerCase().replace(/[^a-z0-9]/g, '-')
  return (
    <AstryxField label={label} inputID={id} width="100%">
      {children}
    </AstryxField>
  )
}

type InputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> & { label?: string }

export function Input({ value, onChange, placeholder, type = 'text', required, disabled, className, label, ...props }: InputProps) {
  return (
    <AstryxTextInput
      label={label ?? ''}
      isLabelHidden={!label}
      width="100%"
      type={type as any}
      value={String(value ?? '')}
      placeholder={placeholder}
      isRequired={required}
      isDisabled={disabled}
      // `isRequired` only sets aria-required; the native attribute is what
      // actually blocks form submission. Astryx doesn't declare it but forwards
      // unknown props to the underlying <input>.
      {...({ required } as any)}
      htmlName={props.name}
      onChange={(val, e) => {
        if (onChange) {
          onChange(e || ({ target: { value: val } } as any))
        }
      }}
      className={className}
      {...props}
    />
  )
}
Input.acceptsFieldLabel = true

type SelectProps = Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'> & { label?: string }

export function Select({
  value,
  onChange,
  children,
  required,
  disabled,
  className,
  label,
  ...props
}: SelectProps) {
  const options: Array<{ value: string; label: string }> = []
  
  React.Children.forEach(children, (child) => {
    if (React.isValidElement(child)) {
      const p = (child as React.ReactElement<{ value?: unknown; children?: unknown }>).props
      const val = String(p.value ?? p.children ?? '')
      const lbl = String(p.children ?? p.value ?? '')
      options.push({ value: val, label: lbl })
    }
  })

  return (
    <AstryxSelector
      label={label ?? ''}
      isLabelHidden={!label}
      width="100%"
      value={String(value ?? '')}
      options={options}
      isRequired={required}
      isDisabled={disabled}
      htmlName={props.name}
      onChange={(val) => {
        if (onChange) {
          onChange({ target: { value: val } } as any)
        }
      }}
      className={className}
      {...props}
    />
  )
}
Select.acceptsFieldLabel = true

export function Textarea({
  value,
  onChange,
  placeholder,
  rows = 3,
  required,
  disabled,
  className,
  label,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string }) {
  return (
    <AstryxTextArea
      label={label ?? ''}
      isLabelHidden={!label}
      width="100%"
      value={String(value ?? '')}
      rows={rows}
      placeholder={placeholder}
      isRequired={required}
      isDisabled={disabled}
      // See Input: only the native attribute gates form submission.
      {...({ required } as any)}
      htmlName={props.name}
      onChange={(val, e) => {
        if (onChange) {
          onChange(e || ({ target: { value: val } } as any))
        }
      }}
      className={className}
      {...props}
    />
  )
}
Textarea.acceptsFieldLabel = true

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
  description,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  description?: string
}) {
  return (
    <AstryxCheckboxInput
      label={label}
      value={checked}
      isDisabled={disabled}
      description={description}
      onChange={(val) => onChange(val)}
    />
  )
}

export function FilePicker({
  label,
  value,
  onChange,
  required,
  accept,
  placeholder,
}: {
  label: string
  /** Pass to control the selection from the parent (e.g. to clear it after upload). */
  value?: File | null
  onChange: (file: File | null) => void
  required?: boolean
  accept?: string
  placeholder?: string
}) {
  const [internalFile, setInternalFile] = useState<File | null>(null)
  const isControlled = value !== undefined
  const file = isControlled ? value : internalFile
  const setFile = (f: File | null) => {
    if (!isControlled) setInternalFile(f)
  }
  return (
    <AstryxFileInput
      label={label}
      value={file}
      isRequired={required}
      accept={accept}
      placeholder={placeholder}
      width="100%"
      onChange={(selected) => {
        const f = Array.isArray(selected) ? selected[0] ?? null : selected
        setFile(f)
        onChange(f)
      }}
    />
  )
}

export function RadioGroup({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string
  onChange: (val: string) => void
  children: ReactNode
}) {
  return (
    <AstryxRadioList label={label} value={value} onChange={onChange} width="100%">
      {children}
    </AstryxRadioList>
  )
}

export function RadioItem({
  label,
  value,
  description,
}: {
  label: string
  value: string
  description?: string
}) {
  return <AstryxRadioListItem label={label} value={value} description={description} />
}

export function Badge({ children, variant }: { children: ReactNode; variant?: 'success' | 'info' | 'warning' | 'error' | 'neutral' }) {
  const text = toText(children)
  let computedVariant: 'success' | 'info' | 'warning' | 'error' | 'neutral' = variant ?? 'neutral'

  if (!variant) {
    if (['ready', 'TRAINABLE', 'approved', 'done'].includes(text)) {
      computedVariant = 'success'
    } else if (['queued', 'building', 'running', 'planned'].includes(text)) {
      computedVariant = 'info'
    } else if (['importing', 'uploading', 'RESERVED_EVALUATION', 'pending'].includes(text)) {
      computedVariant = 'warning'
    } else if (['failed', 'quarantined', 'QUARANTINED', 'rejected'].includes(text)) {
      computedVariant = 'error'
    }
  }

  return <AstryxBadge label={text} variant={computedVariant} />
}

export function Empty({ message }: { message: string }) {
  return (
    <AstryxEmptyState
      title="No data"
      description={message}
    />
  )
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <AstryxBanner
      status="error"
      title="Error"
      description={String((error as Error).message ?? error)}
      container="card"
    />
  )
}
