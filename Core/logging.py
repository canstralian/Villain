#!/usr/bin/env python3
#
# Author: Panagiotis Chartas (t3l3machus) 
#
# This script is part of the "Villain C2 Framework": 
# https://github.com/t3l3machus/Villain

import os
import threading
from .common import system_type
from .concurrency_and_recon import ImplantLoggerController
from .settings import Logging_Settings

main_meta_folder = Logging_Settings.main_meta_folder_unix if system_type in ['Linux', 'Darwin'] else Logging_Settings.main_meta_folder_windows

# Initialize the threading controller for implant logging
_implant_logger_controller = ImplantLoggerController()
_file_lock = threading.Lock()


class HoaxShell_Implants_Logger:

    generated_implants_file = f'{main_meta_folder}/hoaxshell_generated_implants.txt'
    generated_implants_file_open = False  # Keep for backward compatibility


    @staticmethod
    def store_session_details(id, session_meta):

        try:
            # Use proper threading instead of busy-wait
            _implant_logger_controller.set_open()
            try:
                # Use file lock for thread safety
                with _file_lock:
                    with open(HoaxShell_Implants_Logger.generated_implants_file, 'a') as hoaxshell_generated_implants:
                        hoaxshell_generated_implants.write(f'"{id}" : {str(session_meta)}' + ',\n')
                    
                    # Update backward compatibility flag
                    HoaxShell_Implants_Logger.generated_implants_file_open = False
            finally:
                _implant_logger_controller.set_closed()

        except:
            pass



    @staticmethod
    def retrieve_past_sessions_data():

        if os.path.exists(HoaxShell_Implants_Logger.generated_implants_file):

            try:
                # Wait for any ongoing write operations to complete (with timeout)
                _implant_logger_controller.wait_until_closed(timeout=5.0)
                
                # Use file lock for thread safety
                with _file_lock:
                    with open(HoaxShell_Implants_Logger.generated_implants_file, 'r') as hoaxshell_generated_implants:
                        session_data = hoaxshell_generated_implants.read()
                    
                    # Update backward compatibility flag
                    HoaxShell_Implants_Logger.generated_implants_file_open = False
                    return '{' + session_data.strip(',\n') + '}'

            except Exception as e:
                print(e)
                pass
        
        return False
            


def clear_metadata():

    try:
        if os.path.exists(HoaxShell_Implants_Logger.generated_implants_file):
            os.remove(HoaxShell_Implants_Logger.generated_implants_file)
    except:
        return False
    
    return True



# Create folder to store logs and metadata
if os.path.exists(main_meta_folder):
    pass
else:
    os.makedirs(main_meta_folder)
