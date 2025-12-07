#!/usr/bin/env python3
import os
import time
import random
from flask import Flask, request, jsonify
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.flask import FlaskInstrumentor
import threading

# Set random seed for reproducible traces
random.seed(165)

# Configure OpenTelemetry
# Build resource attributes including K8s metadata from environment
resource_attributes = {
    "service.name": "payment-service",
    "k8s.namespace.name": os.environ.get("K8S_NAMESPACE", "app-165"),
    "k8s.deployment.name": "payment-service",
    "k8s.pod.name": os.environ.get("K8S_POD_NAME", "payment-service"),
    "k8s.node.name": os.environ.get("K8S_NODE_NAME", "unknown"),
    "k8s.container.name": "payment-service"
}
resource = Resource.create(resource_attributes)
provider = TracerProvider(resource=resource)
trace.set_tracer_provider(provider)

otlp_exporter = OTLPSpanExporter(
    endpoint="tempo.app-165.svc.cluster.local:4317",
    insecure=True
)
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)

tracer = trace.get_tracer(__name__)


@app.route('/healthz')
def health():
    return jsonify({"status": "healthy"})


@app.route('/payment', methods=['POST'])
def payment():
    with tracer.start_as_current_span("process_payment") as span:
        data = request.json or {}
        
        # Log the incoming request
        print(f"[PAYMENT] Processing payment request for user {data.get('user_id', 'guest')}", flush=True)
        
        # Extract parameters
        user_id = data.get('user_id', 'guest')
        amount = data.get('amount', 0)
        payment_method = data.get('payment_method', 'card')
        
        # Add span attributes
        span.set_attribute("user.id", user_id)
        span.set_attribute("payment.amount", amount)
        span.set_attribute("payment.method", payment_method)
        
        # Simulate database query with variable latency
        with tracer.start_as_current_span("database_query") as db_span:
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.operation", "SELECT")
            
            # Simulate variable latency - some requests are slow
            if random.random() < 0.3:
                # 30% chance of high latency
                query = "SELECT * FROM payments WHERE user_id = ? AND status = 'pending'"
                db_span.set_attribute("db.statement", query)
                sleep_time = random.uniform(1.0, 3.0)  # High latency
                span.set_attribute("latency.high", True)
            else:
                # 70% chance of normal latency
                query = "SELECT * FROM payments WHERE user_id = ?"
                db_span.set_attribute("db.statement", query)
                sleep_time = random.uniform(0.05, 0.2)  # Normal latency
                span.set_attribute("latency.high", False)
            
            time.sleep(sleep_time)
            db_span.set_attribute("db.query.duration_ms", sleep_time * 1000)
        
        # Simulate external API call
        with tracer.start_as_current_span("external_api_call") as api_span:
            api_span.set_attribute("http.method", "POST")
            api_span.set_attribute("http.url", "https://payment-gateway.example.com/charge")
            api_span.set_attribute("http.status_code", 200)
            time.sleep(random.uniform(0.1, 0.5))
        
        response = {
            "transaction_id": f"TXN-{random.randint(10000, 99999)}",
            "status": "success",
            "amount": amount
        }
        
        span.set_attribute("payment.status", "success")
        span.set_attribute("payment.transaction_id", response["transaction_id"])
        
        print(f"[PAYMENT] Completed payment request", flush=True)
        return jsonify(response)


def generate_traffic():
    """Continuously generate traffic to create traces"""
    import requests
    # Wait for Flask to be ready
    time.sleep(5)
    print("[PAYMENT] Starting traffic generator...", flush=True)
    
    while True:
        try:
            data = {
                "user_id": f"user-{random.randint(1000, 9999)}",
                "amount": round(random.uniform(10, 1000), 2),
                "payment_method": random.choice(["card", "paypal", "bank_transfer"])
            }
            
            response = requests.post(
                "http://localhost:8080/payment",
                json=data,
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"[PAYMENT] Generated trace for payment", flush=True)
            else:
                print(f"[PAYMENT] Error generating trace: {response.status_code}", flush=True)
        except Exception as e:
            print(f"[PAYMENT] Error in traffic generator: {e}", flush=True)
        
        # Wait before next request
        time.sleep(random.uniform(2, 5))


if __name__ == '__main__':
    print("[PAYMENT] Starting payment service on port 8080", flush=True)
    
    # Start traffic generator in background
    traffic_thread = threading.Thread(target=generate_traffic, daemon=True)
    traffic_thread.start()
    
    # Start Flask app
    app.run(host='0.0.0.0', port=8080)

