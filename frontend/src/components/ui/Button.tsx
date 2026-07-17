/** HRBP AI Workbench — Button component.
 *
 * Variants: primary, secondary, danger, ghost
 * Sizes: sm, md (default), lg
 * States: loading, disabled
 */

import { ButtonHTMLAttributes, forwardRef } from 'react';
import clsx from 'clsx';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
type ButtonSize = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

const baseStyles = 'inline-flex items-center justify-center font-medium transition-all duration-fast disabled:opacity-50 disabled:cursor-not-allowed';

const variantStyles: Record<ButtonVariant, string> = {
  primary: 'bg-primary-500 text-white hover:bg-primary-600 active:bg-primary-700',
  secondary: 'bg-white text-neutral-600 border border-neutral-200 hover:bg-neutral-50 hover:border-neutral-300',
  danger: 'bg-danger-500 text-white hover:bg-danger-600 active:bg-danger-700',
  ghost: 'text-neutral-500 hover:bg-neutral-50 hover:text-neutral-700',
};

const sizeStyles: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-caption rounded-md gap-1.5',
  md: 'h-10 px-5 text-body rounded-lg gap-2',
  lg: 'h-12 px-6 text-card-title rounded-lg gap-2',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', loading, disabled, className, children, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={clsx(baseStyles, variantStyles[variant], sizeStyles[size], className)}
      {...props}
    >
      {loading && (
        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {children}
    </button>
  )
);

Button.displayName = 'Button';
