/** HRBP AI Workbench — Input component. */

import { InputHTMLAttributes, forwardRef } from 'react';
import clsx from 'clsx';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  error?: string;
  label?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ error, label, className, id, ...props }, ref) => (
    <div className="w-full">
      {label && (
        <label htmlFor={id} className="block text-body-sm text-neutral-500 mb-1.5">
          {label}
        </label>
      )}
      <input
        ref={ref}
        id={id}
        className={clsx(
          'input-base w-full',
          error && 'border-danger-300 focus:border-danger-400 focus:shadow-[0_0_0_3px_var(--color-danger-100)]',
          className,
        )}
        {...props}
      />
      {error && <p className="text-caption text-danger-500 mt-1">{error}</p>}
    </div>
  )
);

Input.displayName = 'Input';
