#!/usr/bin/env python3
"""Entry point: python pr_reviewer.py <PR_URL> --api-key KEY"""
import sys

from reviewer.cli import main

if __name__ == "__main__":
    sys.exit(main())
