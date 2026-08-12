"""
Utility functions and helpers for OpenPilot.

This package contains reusable utilities inspired by Claude Code best practices:
- cache: Memoization and caching decorators
- json_utils: Efficient JSONL processing
- text_utils: String manipulation and CJK support
- concurrency: Async control and timeout management
- data_structures: Circular buffers and accumulators
- formatters: Output formatting utilities
- diff_utils: Diff and patch generation
- tree_viz: Tree visualization for nested structures
"""

__version__ = "0.1.0.dev11"

# Import commonly used utilities for convenience
from .cache import (
    LRUCache,
    TTLCache,
    AsyncTTLCache,
    memoize_with_lru,
    memoize_with_ttl,
    memoize_with_ttl_async,
)

from .json_utils import (
    safe_parse_json,
    parse_jsonl,
    read_jsonl_file,
    append_jsonl,
    read_last_n_lines,
    parse_last_n_jsonl,
    count_jsonl_lines,
    truncate_jsonl_file,
    validate_jsonl_file,
)

from .text_utils import (
    truncate_middle,
    truncate_to_bytes,
    safe_join_lines,
    normalize_cjk_text,
    count_graphemes,
    escape_regex,
    capitalize_first,
    plural,
    normalize_whitespace,
    remove_ansi_codes,
    truncate_lines,
    indent_text,
    extract_between,
    split_preserve_quotes,
)

from .concurrency import (
    CancellationToken,
    create_child_cancel_token,
    sleep_with_cancel,
    async_sleep_with_cancel,
    with_timeout,
    with_timeout_async,
    sequential,
    sequential_async,
    RateLimiter,
    rate_limited,
    Debouncer,
    retry_with_backoff,
)

from .data_structures import (
    CircularBuffer,
    EndTruncatingAccumulator,
)

from .formatters import (
    format_file_size,
    format_duration,
    format_seconds_short,
    format_number_compact,
    format_tokens,
    format_relative_time,
    format_percentage,
    format_log_attributes,
    format_count,
    format_list,
)

from .diff_utils import (
    get_patch_from_contents,
    count_lines_changed,
    apply_patch,
    get_diff_stats,
    format_diff_stats,
    get_changed_lines,
    highlight_diff,
    is_whitespace_only_change,
    get_similarity_ratio,
)

from .tree_viz import (
    treeify,
    treeify_compact,
    treeify_with_types,
    format_tree_node,
)

from .input_utils import (
    read_text,
    read_confirm,
)

__all__ = [
    # Cache
    'LRUCache',
    'TTLCache',
    'AsyncTTLCache',
    'memoize_with_lru',
    'memoize_with_ttl',
    'memoize_with_ttl_async',
    # JSON
    'safe_parse_json',
    'parse_jsonl',
    'read_jsonl_file',
    'append_jsonl',
    'read_last_n_lines',
    'parse_last_n_jsonl',
    'count_jsonl_lines',
    'truncate_jsonl_file',
    'validate_jsonl_file',
    # Text
    'truncate_middle',
    'truncate_to_bytes',
    'safe_join_lines',
    'normalize_cjk_text',
    'count_graphemes',
    'escape_regex',
    'capitalize_first',
    'plural',
    'normalize_whitespace',
    'remove_ansi_codes',
    'truncate_lines',
    'indent_text',
    'extract_between',
    'split_preserve_quotes',
    # Concurrency
    'CancellationToken',
    'create_child_cancel_token',
    'sleep_with_cancel',
    'async_sleep_with_cancel',
    'with_timeout',
    'with_timeout_async',
    'sequential',
    'sequential_async',
    'RateLimiter',
    'rate_limited',
    'Debouncer',
    'retry_with_backoff',
    # Data Structures
    'CircularBuffer',
    'EndTruncatingAccumulator',
    # Formatters
    'format_file_size',
    'format_duration',
    'format_seconds_short',
    'format_number_compact',
    'format_tokens',
    'format_relative_time',
    'format_percentage',
    'format_log_attributes',
    'format_count',
    'format_list',
    # Diff Utils
    'get_patch_from_contents',
    'count_lines_changed',
    'apply_patch',
    'get_diff_stats',
    'format_diff_stats',
    'get_changed_lines',
    'highlight_diff',
    'is_whitespace_only_change',
    'get_similarity_ratio',
    # Tree Visualization
    'treeify',
    'treeify_compact',
    'treeify_with_types',
    'format_tree_node',
    # Input
    'read_text',
    'read_confirm',
]
