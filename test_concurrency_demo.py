#!/usr/bin/env python3
"""
Demo script showing the replacement of busy-wait patterns with proper threading primitives.
This demonstrates how to integrate the new concurrency_and_recon module into Villain.
"""

import tempfile
import threading
import time
from Core.concurrency_and_recon import JsonlLogger, ImplantLoggerController, analyze_phone_number, geolocate_ip

def main():
    print("=== Villain Concurrency Enhancement Demo ===\n")
    
    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_file = f.name
    
    # Initialize components
    jsonl_logger = JsonlLogger(log_file)
    controller = ImplantLoggerController()
    
    print("1. Testing thread-safe JSONL logging...")
    
    def writer_worker(worker_id):
        """Simulate a worker thread writing session data"""
        controller.set_open()
        try:
            session_data = {
                'worker_id': worker_id,
                'timestamp': time.time(),
                'status': 'active'
            }
            jsonl_logger.store_session_details(f'session-{worker_id}', session_data)
            time.sleep(0.1)  # Simulate work
            print(f"   Worker {worker_id} completed logging")
        finally:
            controller.set_closed()
    
    def reader_worker():
        """Simulate a reader waiting for log file to be available"""
        print("   Reader waiting for log file to be closed...")
        success = controller.wait_until_closed(timeout=5.0)
        if success:
            print("   Reader can now safely read the log file")
        else:
            print("   Reader timed out waiting for log file")
    
    # Start multiple writer threads and a reader
    writers = [threading.Thread(target=writer_worker, args=(i,)) for i in range(3)]
    reader = threading.Thread(target=reader_worker)
    
    for w in writers:
        w.start()
    reader.start()
    
    for w in writers:
        w.join()
    reader.join()
    
    print("   ✓ Thread-safe logging completed\n")
    
    print("2. Testing phone number analysis...")
    phone_result = analyze_phone_number("+14155552671")
    if phone_result:
        print(f"   ✓ Phone number valid: {phone_result['valid']}")
        print(f"   ✓ Location: {phone_result.get('location', 'Unknown')}")
        print(f"   ✓ Format: {phone_result['e164']}")
    else:
        print("   ✗ Phone analysis failed")
    
    print("\n3. Testing IP geolocation...")
    # Note: This will make an actual API call, so we use a well-known IP
    ip_result = geolocate_ip("8.8.8.8")
    if ip_result:
        print(f"   ✓ IP: {ip_result['ip']}")
        print(f"   ✓ Country: {ip_result.get('country', 'Unknown')}")
        print(f"   ✓ ISP: {ip_result.get('isp', 'Unknown')}")
    else:
        print("   ✗ Geolocation failed (possibly due to network restrictions)")
    
    print("\n4. Demonstrating busy-wait replacement:")
    print("   Old pattern: while flag: pass")
    print("   New pattern: controller.wait_until_closed(timeout=5)")
    print("   ✓ No more CPU-intensive busy waiting!")
    
    # Cleanup
    jsonl_logger.close(2.0)
    
    print(f"\n=== Demo completed successfully ===")
    print(f"Log file created at: {log_file}")
    
    # Show log contents
    try:
        with open(log_file, 'r') as f:
            content = f.read().strip()
            if content:
                print("\nLog file contents:")
                for line in content.split('\n'):
                    print(f"   {line}")
            else:
                print("Log file is empty")
    except Exception as e:
        print(f"Error reading log file: {e}")

if __name__ == "__main__":
    main()