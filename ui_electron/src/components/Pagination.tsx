/**
 * Pagination Component
 * 
 * Provides consistent pagination controls with page size selector,
 * prev/next buttons, and page information display.
 */

interface PaginationProps {
    page: number;
    pageSize: number;
    total: number;
    onPageChange: (page: number) => void;
    onPageSizeChange: (size: number) => void;
    pageSizeOptions?: number[];
}

export function Pagination({
    page,
    pageSize,
    total,
    onPageChange,
    onPageSizeChange,
    pageSizeOptions = [20, 25, 50, 100, 200]
}: PaginationProps) {
    const totalPages = Math.ceil(total / pageSize);
    const startItem = (page - 1) * pageSize + 1;
    const endItem = Math.min(page * pageSize, total);

    const canGoPrev = page > 1;
    const canGoNext = page < totalPages;

    return (
        <div className="pagination-container">
            <div className="pagination-left">
                <select
                    value={pageSize}
                    onChange={e => onPageSizeChange(Number(e.target.value))}
                    className="pagination-select"
                >
                    {pageSizeOptions.map(size => (
                        <option key={size} value={size}>{size} per page</option>
                    ))}
                </select>
                <span className="pagination-info">
                    Showing {startItem} - {endItem} of {total}
                </span>
            </div>
            <div className="pagination-right">
                <button
                    disabled={!canGoPrev}
                    onClick={() => onPageChange(page - 1)}
                    className="pagination-button"
                >
                    &lt; Prev
                </button>
                <span className="pagination-page">
                    Page {page} of {totalPages}
                </span>
                <button
                    disabled={!canGoNext}
                    onClick={() => onPageChange(page + 1)}
                    className="pagination-button"
                >
                    Next &gt;
                </button>
            </div>
        </div>
    );
}
