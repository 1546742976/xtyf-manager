"""Start durable invoice dispatchers after fork, including --preload deployments."""


def post_worker_init(worker):
    from modules.invoices import start_invoice_worker
    start_invoice_worker()
