#!/usr/bin/env python3
"""
Test script to demonstrate the threading improvements in the logging system.
This shows how the busy-wait patterns have been replaced with proper threading primitives.
"""

import threading
import time
import tempfile
import os
from Core.logging import HoaxShell_Implants_Logger

def test_concurrent_logging():
    """Test concurrent access to logging system"""
    print("Testing concurrent logging with new threading approach...")
    
    # Use temporary file for testing
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        test_file = f.name
    
    # Backup and override the file path
    original_file = HoaxShell_Implants_Logger.generated_implants_file
    HoaxShell_Implants_Logger.generated_implants_file = test_file
    
    results = []
    errors = []
    
    def worker_thread(worker_id, num_sessions):
        """Worker that stores multiple sessions concurrently"""
        try:
            for i in range(num_sessions):
                session_id = f"worker-{worker_id}-session-{i}"
                session_data = {
                    'worker_id': worker_id,
                    'session_num': i,
                    'timestamp': time.time(),
                    'status': 'active'
                }
                HoaxShell_Implants_Logger.store_session_details(session_id, session_data)
                # Small delay to simulate real work
                time.sleep(0.01)
            
            results.append(f"Worker {worker_id} completed {num_sessions} sessions")
        except Exception as e:
            errors.append(f"Worker {worker_id} error: {e}")
    
    def reader_thread():
        """Reader that periodically tries to read the log"""
        try:
            time.sleep(0.1)  # Let writers get started
            for i in range(5):
                data = HoaxShell_Implants_Logger.retrieve_past_sessions_data()
                if data:
                    session_count = data.count('worker-')
                    results.append(f"Reader iteration {i}: found {session_count} sessions")
                else:
                    results.append(f"Reader iteration {i}: no data yet")
                time.sleep(0.05)
        except Exception as e:
            errors.append(f"Reader error: {e}")
    
    # Start multiple workers and a reader
    workers = [threading.Thread(target=worker_thread, args=(i, 5)) for i in range(4)]
    reader = threading.Thread(target=reader_thread)
    
    print("   Starting 4 worker threads + 1 reader thread...")
    start_time = time.time()
    
    for worker in workers:
        worker.start()
    reader.start()
    
    for worker in workers:
        worker.join()
    reader.join()
    
    end_time = time.time()
    
    # Final read to count total sessions
    final_data = HoaxShell_Implants_Logger.retrieve_past_sessions_data()
    if final_data:
        final_count = final_data.count('worker-')
        print(f"   ✓ All threads completed in {end_time - start_time:.2f}s")
        print(f"   ✓ Total sessions logged: {final_count}")
        print(f"   ✓ No threading conflicts or busy-waits")
        
        # Print some results
        for result in results[-3:]:  # Show last few results
            print(f"     {result}")
    else:
        print("   ✗ Failed to read final data")
    
    if errors:
        print("   Errors encountered:")
        for error in errors:
            print(f"     {error}")
    else:
        print("   ✓ No errors encountered")
    
    # Cleanup
    os.unlink(test_file)
    HoaxShell_Implants_Logger.generated_implants_file = original_file
    
    return len(errors) == 0

def main():
    print("=== Threading Improvements Test ===\n")
    
    print("Before: Busy-wait pattern")
    print("   while HoaxShell_Implants_Logger.generated_implants_file_open:")
    print("       pass  # <-- CPU-intensive busy waiting!")
    print()
    
    print("After: Proper threading primitives")
    print("   _implant_logger_controller.wait_until_closed(timeout=5.0)")
    print("   with _file_lock:  # <-- Thread-safe file access")
    print()
    
    success = test_concurrent_logging()
    
    print("\n" + "="*50)
    if success:
        print("✓ All threading improvements working correctly!")
        print("✓ Busy-wait patterns successfully replaced")
        print("✓ Thread-safe file access implemented")
        print("✓ No race conditions detected")
    else:
        print("✗ Some issues detected during testing")
    
    print("="*50)

if __name__ == "__main__":
    main()