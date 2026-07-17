/** HRBP AI Workbench — Skeleton loading placeholder. */

import { HTMLAttributes } from 'react';
import clsx from 'clsx';

interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'text' | 'circular' | 'rectangular';
  width?: string | number;
  height?: string | number;
}

export function Skeleton({ variant = 'text', width, height, className, style, ...props }: SkeletonProps) {
  const variantStyles = {
    text: 'h-4 rounded-md',
    circular: 'rounded-full',
    rectangular: 'rounded-lg',
  };

  const defaultSize = {
    text: { width: '100%', height: undefined },
    circular: { width: 40, height: 40 },
    rectangular: { width: '100%', height: 120 },
  };

  return (
    <div
      className={clsx(
        'animate-pulse bg-neutral-200',
        variantStyles[variant],
        className,
      )}
      style={{
        width: width ?? defaultSize[variant].width,
        height: height ?? defaultSize[variant].height,
        ...style,
      }}
      {...props}
    />
  );
}
