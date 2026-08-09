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
          <Heading level={2}>{title}</Heading>
          {subtitle && (
            <Text type="supporting" color="secondary">
              {subtitle}
            </Text>
          )}
        </VStack>
        {actions && <HStack gap={2}>{actions}</HStack>}
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

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <AstryxCard width="100%" padding={4}>
      <VStack gap={1}>
        <Text type="supporting" color="secondary" weight="medium">
          {label.toUpperCase()}
        </Text>
        <Heading level={3}>
          {value}
        </Heading>
      </VStack>
    </AstryxCard>
  )
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost' | 'danger'
  label?: string
}

export function Button({ variant = 'primary', className = '', label, children, ...props }: ButtonProps) {
  const astryxVariant = variant === 'danger' ? 'destructive' : variant === 'ghost' ? 'ghost' : 'primary'
  const buttonLabel = label || (typeof children === 'string' ? children : '')
  
  return (
    <AstryxButton
      label={buttonLabel || 'Action'}
      variant={astryxVariant}
      isDisabled={props.disabled}
      type={props.type as any}
      onClick={props.onClick as any}
      className={className}
    >
      {buttonLabel ? null : children}
    </AstryxButton>
  )
}

export function Field({ label, children, inputID }: { label: string; children: ReactNode; inputID?: string }) {
  const id = inputID || label.toLowerCase().replace(/[^a-z0-9]/g, '-')
  return (
    <AstryxField label={label} inputID={id} width="100%">
      {children}
    </AstryxField>
  )
}

export function Input({ value, onChange, placeholder, type = 'text', required, disabled, className, size: _size, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  const id = props.id || props.name || undefined
  return (
    <AstryxTextInput
      id={id}
      label=""
      isLabelHidden
      width="100%"
      type={type as any}
      value={String(value ?? '')}
      placeholder={placeholder}
      isRequired={required}
      isDisabled={disabled}
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

export function Select({
  value,
  onChange,
  children,
  required,
  disabled,
  className,
  size: _size,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  const options: Array<{ value: string; label: string }> = []
  
  React.Children.forEach(children, (child) => {
    if (React.isValidElement(child)) {
      const p = (child as React.ReactElement<{ value?: unknown; children?: unknown }>).props
      const val = String(p.value ?? p.children ?? '')
      const lbl = String(p.children ?? p.value ?? '')
      options.push({ value: val, label: lbl })
    }
  })

  const id = props.id || props.name || undefined

  return (
    <AstryxSelector
      id={id}
      label=""
      isLabelHidden
      width="100%"
      value={String(value ?? '')}
      options={options}
      isRequired={required}
      isDisabled={disabled}
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

export function Textarea({
  value,
  onChange,
  placeholder,
  rows = 3,
  required,
  disabled,
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = props.id || props.name || undefined
  return (
    <AstryxTextArea
      id={id}
      label=""
      isLabelHidden
      width="100%"
      value={String(value ?? '')}
      rows={rows}
      placeholder={placeholder}
      isRequired={required}
      isDisabled={disabled}
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
  onChange,
  required,
  accept,
  placeholder,
}: {
  label: string
  onChange: (file: File | null) => void
  required?: boolean
  accept?: string
  placeholder?: string
}) {
  const [file, setFile] = useState<File | null>(null)
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
  const text = String(children ?? '')
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
