import pytest
import tempfile
import os
from app.core.file_editor.file_editor import apply_edits_to_file, parse_edit_blocks
# Assuming the file editor code is in file_editor.py
# from file_editor import apply_edits_to_file, apply_edits_to_file, parse_edit_blocks


class TestFileEditor:
    
    @pytest.fixture
    def temp_file(self):
        """Create a temporary file for testing"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.py') as f:
            f.write("""def hello():
    return "world"

class MyClass:
    def __init__(self):
        self.value = 42
    
    def get_value(self):
        return self.value

# End of file""")
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    
    def test_basic_search_replace(self, temp_file):
        """Test basic search and replace functionality"""
        edit_string = """
<<<<<<< SEARCH
def hello():
    return "world"
=======
def hello():
    return "universe"
>>>>>>> REPLACE
"""
        
        result = apply_edits_to_file(temp_file, edit_string)
        assert 'return "universe"' in result
        assert 'return "world"' not in result
    
    def test_multiple_edits(self, temp_file):
        """Test multiple edits in single string"""
        edit_string = """
<<<<<<< SEARCH
def hello():
    return "world"
=======
def hello():
    return "universe"
>>>>>>> REPLACE

<<<<<<< SEARCH
        self.value = 42
=======
        self.value = 100
>>>>>>> REPLACE
"""
        
        result = apply_edits_to_file(temp_file, edit_string)
        assert 'return "universe"' in result
        assert 'self.value = 100' in result
        assert 'return "world"' not in result
        assert 'self.value = 42' not in result
    
    def test_append_to_end_of_file(self, temp_file):
        """Test appending content to end of file"""
        edit_string = """
<<<<<<< SEARCH
<<<END_OF_FILE>>>
=======

def new_function():
    return "appended"
>>>>>>> REPLACE
"""
        
        result = apply_edits_to_file(temp_file, edit_string)
        assert result.endswith('\ndef new_function():\n    return "appended"')
        assert 'def hello():' in result  # Original content preserved
    
    def test_prepend_to_start_of_file(self, temp_file):
        """Test prepending content to start of file"""
        edit_string = """
<<<<<<< SEARCH
<<<START_OF_FILE>>>
=======
# Header comment
import os

>>>>>>> REPLACE
"""
        
        result = apply_edits_to_file(temp_file, edit_string)
        assert result.startswith('# Header comment\nimport os\ndef hello():')
        assert 'class MyClass:' in result  # Original content preserved
    
    def test_search_text_not_found(self, temp_file):
        """Test error when search text doesn't exist"""
        edit_string = """
<<<<<<< SEARCH
def nonexistent_function():
    pass
=======
def replacement():
    pass
>>>>>>> REPLACE
"""
        
        with pytest.raises(ValueError, match="Search text not found"):
            apply_edits_to_file(temp_file, edit_string)
    
    def test_non_unique_search_text(self, temp_file):
        """Test error when search text appears multiple times"""
        # First add duplicate content
        with open(temp_file, 'a') as f:
            f.write('\n\ndef hello():\n    return "duplicate"')
        
        edit_string = """
<<<<<<< SEARCH
def hello():
=======
def modified_hello():
>>>>>>> REPLACE
"""
        
        with pytest.raises(ValueError, match="Search text appears .* times"):
            apply_edits_to_file(temp_file, edit_string)
    
    def test_invalid_edit_format(self, temp_file):
        """Test error with malformed edit string"""
        invalid_edit = """
<<<<<<< SEARCH
def hello():
    return "world"
# Missing separator and end markers
"""
        
        with pytest.raises(ValueError, match="No valid edit blocks found"):
            apply_edits_to_file(temp_file, invalid_edit)
    
    def test_file_not_found(self):
        """Test error when file doesn't exist"""
        edit_string = """
<<<<<<< SEARCH
anything
=======
replacement
>>>>>>> REPLACE
"""
        
        with pytest.raises(FileNotFoundError):
            apply_edits_to_file("/nonexistent/file.py", edit_string)
    
    def test_whitespace_preservation(self, temp_file):
        """Test that whitespace is preserved correctly"""
        edit_string = """
<<<<<<< SEARCH
    def get_value(self):
        return self.value
=======
    def get_value(self):
        return self.value * 2
>>>>>>> REPLACE
"""
        
        result = apply_edits_to_file(temp_file, edit_string)
        assert '    def get_value(self):' in result
        assert '        return self.value * 2' in result
    
    def test_empty_replacement(self, temp_file):
        """Test replacing text with empty string (deletion)"""
        edit_string = """
<<<<<<< SEARCH
class MyClass:
    def __init__(self):
        self.value = 42
    
    def get_value(self):
        return self.value
=======
>>>>>>> REPLACE
"""
        
        result = apply_edits_to_file(temp_file, edit_string)
        assert 'class MyClass:' not in result
        assert 'def hello():' in result  # Other content preserved
    
    def test_apply_edits_to_file(self, temp_file):
        """Test the write-back functionality"""
        edit_string = """
<<<<<<< SEARCH
# End of file
=======
# Modified end of file
>>>>>>> REPLACE
"""
        
        apply_edits_to_file(temp_file, edit_string)
        
        # Read the file back and verify
        with open(temp_file, 'r') as f:
            content = f.read()
        
        assert '# Modified end of file' in content
        assert '# End of file' not in content
    
    def test_parse_edit_blocks(self):
        """Test the edit block parser"""
        edit_string = """
<<<<<<< SEARCH
search1
=======
replace1
>>>>>>> REPLACE

<<<<<<< SEARCH  
search2
=======
replace2
>>>>>>> REPLACE
"""
        
        blocks = parse_edit_blocks(edit_string)
        assert len(blocks) == 2
        assert blocks[0] == ('search1', 'replace1')
        assert blocks[1] == ('search2', 'replace2')
    
    def test_complex_multiline_edit(self, temp_file):
        """Test complex multiline search and replace"""
        edit_string = """
<<<<<<< SEARCH
class MyClass:
    def __init__(self):
        self.value = 42
    
    def get_value(self):
        return self.value
=======
class MyClass:
    def __init__(self, initial_value=42):
        self.value = initial_value
        self.history = []
    
    def get_value(self):
        return self.value
    
    def set_value(self, new_value):
        self.history.append(self.value)
        self.value = new_value
>>>>>>> REPLACE
"""
        
        result = apply_edits_to_file(temp_file, edit_string)
        assert 'initial_value=42' in result
        assert 'self.history = []' in result
        assert 'def set_value(self, new_value):' in result


if __name__ == "__main__":
    # Run tests with: python -m pytest test_file_editor.py -v
    pytest.main([__file__, "-v"])