/** HRBP AI Workbench — Card component. */

import { HTMLAttributes, forwardRef } from 'react';
import clsx from 'clsx';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: 'none' | 'sm' | 'md';
  hover?: boolean;
}

const paddingStyles = {
  none: '',
  sm: 'p-4',
  md: 'p-5',
};

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ padding = 'md', hover = true, className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={clsx(
        'bg-white border border-neutral-200 rounded-xl transition-all duration-normal',
        paddingStyles[padding],
        hover && 'hover:border-neutral-300 hover:shadow-sm',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  )
);

Card.displayName = 'Card';
