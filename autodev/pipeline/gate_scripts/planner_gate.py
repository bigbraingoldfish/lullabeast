import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils import ARTIFACTS_DIR, load_json_safe, record_error_code_only, ERR_VALIDATION_FAILED

def evaluate_planner(output_path=None):
    if output_path is None:
        output_path = os.path.join(ARTIFACTS_DIR, "planner_output.json")
        
    data = load_json_safe(output_path, "planner")
    if data is None: 
        return "FAIL"
        
    # Structural checks
    valid = True
    if not data.get("implementation_plan") or not isinstance(data["implementation_plan"], list):
        valid = False
    elif not data.get("tdd_test_structure") or not isinstance(data["tdd_test_structure"], list):
        valid = False
    
    pass_criteria = data.get("pass_criteria")
    if not pass_criteria or not isinstance(pass_criteria, list) or len(pass_criteria) < 1: 
        valid = False
    else:
        for crit in pass_criteria:
            if not isinstance(crit, dict) or "condition" not in crit:
                valid = False
                break
            
    if not valid:
        record_error_code_only("planner", ERR_VALIDATION_FAILED)
        return "FAIL"
        
    return "PASS"

if __name__ == "__main__":
    result = evaluate_planner(sys.argv[1] if len(sys.argv) > 1 else None)
    print(result)
