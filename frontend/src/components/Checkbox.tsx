import * as RadixCheckbox from '@radix-ui/react-checkbox'

interface Props {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: React.ReactNode
  id?: string
}

export function Checkbox({ checked, onChange, label, id }: Props) {
  const checkId = id ?? `cb-${Math.random().toString(36).slice(2)}`
  return (
    <div className="flex items-center gap-3">
      <RadixCheckbox.Root
        id={checkId}
        checked={checked}
        onCheckedChange={v => onChange(!!v)}
        className="w-5 h-5 rounded-md border-2 border-gray-300 bg-white flex items-center justify-center
                   data-[state=checked]:bg-blue-600 data-[state=checked]:border-blue-600
                   hover:border-blue-400 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-1
                   flex-shrink-0 cursor-pointer"
      >
        <RadixCheckbox.Indicator>
          <svg className="w-3 h-3 text-white" viewBox="0 0 12 12" fill="none">
            <path d="M2 6.5L4.5 9 10 3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </RadixCheckbox.Indicator>
      </RadixCheckbox.Root>
      {label && (
        <label htmlFor={checkId} className="cursor-pointer select-none">
          {label}
        </label>
      )}
    </div>
  )
}
