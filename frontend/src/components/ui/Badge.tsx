/** HRBP AI Workbench — Badge component.
 *
 * Variants map to semantic colors from the design system.
 */

import { HTMLAttributes } from 'react';
import clsx from 'clsx';

type BadgeVariant = 'default' | 'primary' | 'success' | 'warning' | 'danger' | 'info';

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: 'sm' | 'md';
}

const variantStyles: Record<BadgeVariant, string> = {
  default: 'bg-neutral-100 text-neutral-600',
  primary: 'bg-primary-50 text-primary-600',
  success: 'bg-emerald-50 text-emerald-600',
  warning: 'bg-warning-50 text-warning-600',
  danger: 'bg-danger-50 text-danger-600',
  info: 'bg-accent-50 text-accent-600',
};

const sizeStyles = {
  sm: 'px-2 py-0.5 text-caption',
  md: 'px-2.5 py-1 text-caption',
};

export function Badge({ variant = 'default', size = 'sm', className, children, ...props }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-full font-medium',
        variantStyles[variant],
        sizeStyles[size],
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}
