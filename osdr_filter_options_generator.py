#!/usr/bin/env python3
"""
NASA OSDR Dashboard JSON Generator - Final Version
===================================================
Generates filter-options.json from OSDR API with intelligent categorization.

Key Features:
- New Assay structure: Measurement Type -> Technology Type -> Platform
- Smart material categorization (laterality, tissue specificity)
- Taxonomic organism classification (bacteria, plants, fungi, wasps)
- Case-insensitive matching throughout
- Preserves all existing values

Usage:
    python3 osdr_generator.py
"""

import copy
from enum import Enum
import json
import sys
import os
import requests
import re
from collections import defaultdict

DEBUG = True
FILTER_GROUPINGS_TO_UPDATE = ['Project Type', 'Assay Measurement Type', 'Factor', 'Organism', 'Material Type', 'Mission']
# 'Assay Technology Type', 'Assay Device Platform'

class MainGroup(Enum):
    ASSAY_MEASUREMENT = 'Assay Measurement Type'
    ASSAY_TECHNOLOGY = 'Assay Technology Type'
    ASSAY_PLATFORM = 'Assay Device Platform'
    FACTOR = 'Factor'
    ORGANISM = 'organism'
    MATERIAL_TYPE = 'Material Type'
    MISSION = 'Mission'

class SmartCategorizer:
    """Intelligent categorization helper"""
    
    # Taxonomic databases
    BACTERIA_GENERA = {
        'Agrobacterium', 'Bacillus', 'Escherichia', 'Pseudomonas',
        'Salmonella', 'Staphylococcus', 'Streptococcus', 'Enterobacter',
        'Klebsiella', 'Serratia', 'Vibrio', 'Paraburkholderia',
        'Burkholderia', 'Rhizobium', 'Sinorhizobium'
    }
    
    FUNGUS_GENERA = {
        'Aspergillus', 'Candida', 'Fusarium', 'Penicillium',
        'Saccharomyces', 'Neurospora'
    }
    
    PLANT_GENERA = {
        'Arabidopsis', 'Brassica', 'Capsicum', 'Citrus', 'Daucus',
        'Glycine', 'Lactuca', 'Lolium', 'Marchantia', 'Oryza',
        'Raphanus', 'Solanum', 'Triticum', 'Zea'
    }
    
    WASP_GENERA = {
        'Leptopilina', 'Nasonia'
    }
    
    @staticmethod
    def normalize(s):
        """Normalize string for comparison"""
        if not s or not isinstance(s, str):
            return ""
        return s.strip().lower()
    
    @staticmethod
    def get_taxonomy_category(organism_name):
        """
        Classify organism by taxonomy.
        Returns (type, full_category) or None.
        """
        if not organism_name:
            return None
        
        parts = organism_name.split()
        if not parts:
            return None
        
        genus = parts[0]
        
        if genus in SmartCategorizer.BACTERIA_GENERA:
            return ('bacteria', f'bacteria|{organism_name}')
        if genus in SmartCategorizer.FUNGUS_GENERA:
            return ('fungus', f'fungus|{organism_name}')
        if genus in SmartCategorizer.PLANT_GENERA:
            return ('plant', f'plant|{organism_name}')
        if genus in SmartCategorizer.WASP_GENERA:
            return ('wasp', f'wasp|{organism_name}')
        
        return None
    
    @staticmethod
    def match_material_to_existing(value, material_type_grouping):
        """
        Match material to existing categories using smart patterns.
        Handles: laterality, case differences, anatomical keywords
        Special: 3-tier hierarchy for muscles (muscle|muscle_name|laterality)
        """
        norm_val = SmartCategorizer.normalize(value)
        
        # Define muscle types that get 3-tier hierarchy
        muscle_types = [
            'gastrocnemius', 'soleus', 'tibialis anterior', 'quadriceps femoris', 
            'extensor digitorum longus', 'quadriceps', 'vastus lateralis', 'calf muscle'
        ]
        
        # First: Try exact match (case-insensitive)
        if OSDRFilterGenerator.is_value_in_entry_children(norm_val, material_type_grouping, True):
            return ''
        
        # Second: Laterality patterns (left/right/both + term, including "both sides")
        # Matches: "left X", "right X", "both X", "X - both sides", "X- both sides", "X both sides"
        laterality_match = re.match(r'^(left|right|both)\s+(.+)$', norm_val)
        both_sides_match = re.match(r'^(.+?)[\s\-]*both\s*sides$', norm_val)
        
        if laterality_match:
            laterality = laterality_match.group(1)
            base_term = laterality_match.group(2).strip()
            
            # Check if this is a muscle
            matched_muscle = None
            for muscle in muscle_types:
                if muscle in base_term:
                    matched_muscle = muscle
                    break
            
            if matched_muscle:
                # Muscles use 2-tier, not 3-tier: muscle|muscle_name
                # The lateralized value goes INTO this category as a value
                parent = OSDRFilterGenerator.get_child_from_parent('muscle', material_type_grouping)
                if not parent:
                    parent = OSDRFilterGenerator.append_new_main_entry('muscle', material_type_grouping)
                child = OSDRFilterGenerator.get_child_from_parent(matched_muscle, parent)
                if not child:
                    child = OSDRFilterGenerator.append_new_main_entry(matched_muscle, parent)
                child['values'].append(norm_val)
                return f"muscle|{matched_muscle}"
            
            # For non-muscles, create 2-tier hierarchy
            # Look for parent category with base term
            parent = OSDRFilterGenerator.get_child_from_parent(base_term, material_type_grouping)
            if parent:
                child = OSDRFilterGenerator.append_new_main_entry(f"{laterality} {base_term}", parent)
                if norm_val not in child['values']:
                    child['values'].append(norm_val)
                return f"{parent}|{laterality} {base_term}"
            
        elif both_sides_match:
            # Handle "X - both sides", "X- both sides", "X both sides"
            base_term = both_sides_match.group(1).strip().rstrip('-').strip()
            
            # Normalize base term for singular/plural
            # "adrenal glands" → "adrenal gland"
            base_term_singular = base_term.rstrip('s') if base_term.endswith('s') else base_term
            
            # Check if this is a muscle
            matched_muscle = None
            for muscle in muscle_types:
                if muscle in base_term:
                    matched_muscle = muscle
                    break
            
            if matched_muscle:
                # Muscles use 2-tier: muscle|muscle_name
                parent = OSDRFilterGenerator.get_child_from_parent('muscle', material_type_grouping)
                if not parent:
                    parent = OSDRFilterGenerator.append_new_main_entry('muscle', material_type_grouping)
                child = OSDRFilterGenerator.get_child_from_parent(matched_muscle, parent)
                if not child:
                    child = OSDRFilterGenerator.append_new_main_entry(matched_muscle, parent)
                child['values'].append(norm_val)
                return f"muscle|{matched_muscle}"
            
            # Check if base_term (or singular) matches a keyword
            # This handles "adrenal glands- both sides" → check "adrenal gland" keyword
            for check_term in [base_term, base_term_singular]:
                parent = OSDRFilterGenerator.is_value_in_entry_children(check_term, material_type_grouping, True, True, True)
                if parent:
                    child = OSDRFilterGenerator.is_value_in_entry_children(f"both {check_term}", parent)
                    if not child:
                        child = OSDRFilterGenerator.append_new_main_entry(f"both {check_term}", parent)
                    child['values'].append(norm_val)
                    category = parent['values'][0]
                    if 'displayValue' in parent:
                        category = parent['displayValue']
                    return f"{category}|both {check_term}"
            
            # No match found, return as-is
            child = OSDRFilterGenerator.append_new_main_entry(f"both {base_term}", material_type_grouping)
            child['values'].append(norm_val)
            return f"both {base_term}"
        
        # Third: Suffix-based categorization (BEFORE general keyword matching)
        # Check for common suffixes that determine categorization
        
        # Rule: ends with "swab" → swab parent category
        if norm_val.endswith(' swab') or norm_val == 'swab':
            # Check if swab category exists
            swab_entry = OSDRFilterGenerator.is_value_in_entry_children('swab', material_type_grouping)
            if swab_entry and norm_val != 'swab':
                # Create sub-category under swab
                child = OSDRFilterGenerator.append_new_main_entry(value, swab_entry)
                return f"swab|{value}"
            else:
                swab_entry['values'].append(norm_val)
                return 'swab'
        
        # Rule: ends with "cells" or "cell" → cells parent category (except blood cells and specific types)
        if norm_val.endswith(' cells') or norm_val.endswith(' cell'):
            # Exception 1: blood cells go to blood parent
            if 'blood' in norm_val:
                blood_entry = OSDRFilterGenerator.is_value_in_entry_children('blood', material_type_grouping)
                
                if blood_entry:
                    # Create sub-category under blood
                    child = OSDRFilterGenerator.append_new_main_entry(value, blood_entry)
                    return f"blood|{value}"
                else:
                    blood_entry['values'].append(norm_val)
                    return 'blood'
            
            # Exception 2: T cells should match keyword first (use word boundaries)
            elif re.search(r'\bt\s+cell', norm_val):
                # Let keyword matching handle this
                pass  # Fall through to keyword matching
            else:
                # Regular cells - go to cells parent
                cells_entry = OSDRFilterGenerator.is_value_in_entry_children('cells', material_type_grouping)
                if cells_entry and norm_val not in ['cell', 'cells']:
                    child = OSDRFilterGenerator.append_new_main_entry(value, cells_entry)
                    return f"cells|{value}"
                else:
                    cells_entry['values'].append(norm_val)
                    return 'cells'
        
        # Rule: ends with "tumor" → tumor parent category
        if norm_val.endswith(' tumor'):
            tumor_entry = OSDRFilterGenerator.is_value_in_entry_children('tumor', material_type_grouping)
            if tumor_entry and norm_val != 'tumor':
                child = OSDRFilterGenerator.append_new_main_entry(value, tumor_entry)
                return f"tumor|{value}"
            else:
                tumor_entry['values'].append(norm_val)
                return 'tumor'
        
        # Fourth: Anatomical keyword mapping (CHECK BEFORE substring matching!)
        anatomical_keywords = {
            # Brain regions
            'cerebellum': 'brain|cerebellum',
            'cerebrum': 'brain|cerebrum',
            'cerebral cortex': 'brain|cerebrum',
            'cerebral hemisphere': 'brain|cerebrum',
            'hippocampus': 'brain|hippocampus',
            'frontal cortex': 'brain|frontal cortex',
            'parietal cortex': 'brain|parietal cortex',
            'cortex': 'brain|cortex',
            'dentate gyrus': 'brain|dentate gyrus of hippocampal formation',
            'subdural space': 'brain|subdural space',
            'forebrain': 'brain|forebrain',
            
            # Heart and cardiovascular
            'ventricle': 'heart',
            'ventricles': 'heart',
            'aorta': 'heart|aorta',
            'left ventricle': 'heart|left ventricle',
            'right ventricle': 'heart|right ventricle',
            'atria': 'heart',
            'atrium': 'heart',
            
            # Muscles
            'gastrocnemius': 'muscle|gastrocnemius',
            'soleus': 'muscle|soleus',
            'tibialis anterior': 'muscle|tibialis anterior',
            'quadriceps': 'muscle|quadriceps femoris',
            'quadriceps muscle': 'muscle|quadriceps femoris',
            'extensor digitorum longus': 'muscle|extensor digitorum longus',
            'extensor digitorum longus- both sides': 'muscle|extensor digitorum longus',
            'extensor digitorum longus - both sides': 'muscle|extensor digitorum longus',
            'vastus lateralis': 'muscle|vastus lateralis',
            'calf muscle': 'muscle|calf muscle',
            'cardiac muscle': 'cardiac muscle tissue',
            
            # Organs - Major
            'liver': 'liver',
            'kidney': 'kidney',
            'spleen': 'spleen',
            'lung': 'lung',
            'adrenal gland': 'adrenal gland',
            'adrenal glands- both sides': 'adrenal gland',
            'adrenal glands - both sides': 'adrenal gland',
            'thymus': 'thymus',
            'thyroid': 'thyroid gland',
            'pituitary': 'pituitary gland',
            'pancreas': 'pancreas',
            'hypothalamus': 'hypothalamus',
            
            # Nervous system
            'optic nerve': 'optic nerve',
            
            # Reproductive organs
            'testis': 'testis',
            'ovary': 'ovary',
            'uterus': 'uterus',
            'prostate': 'prostate',
            'mammary gland': 'mammary gland',
            'mammary': 'mammary gland',
            'placenta': 'placenta',
            'vaginal specimen': 'vaginal specimen',
            'vagina': 'vaginal specimen',
            'zygote': 'zygote',
            
            # Digestive system
            'intestine': 'intestine',
            'small intestine': 'intestine',
            'large intestine': 'intestine|large intestine',
            'duodenum': 'duodenum',
            'jejunum': 'jejunum',
            'ileum': 'ileum',
            'colon': 'colon',
            'descending colon': 'descending colon',
            'intestines': 'intestine',
            'stomach': 'stomach',
            'esophagus': 'esophagus',
            'cecum': 'cecum',
            
            # Diaphragm
            'diaphragm': 'diaphragm',
            
            # Bones and skeletal
            'femur': 'femur',
            'tibia': 'tibia',
            'humerus': 'humerus',
            'calvariae': 'calvariae',
            'calvaria': 'calvariae',
            'vertebrae': 'vertebrae',
            'vertebra': 'vertebrae',
            'lumbar': 'vertebrae',
            'skull': 'skull',
            'parietal bone': 'skull|parietal bone',
            'cortical bone': 'cortical bone',
            'bone marrow': 'bone marrow',
            'bone marrow of femur': 'femur|bone marrow of femur',
            'bone marrow of humerus': 'bone marrow|bone marrow of humerus',
            
            # Adipose tissue
            'adipose tissue': 'adipose tissue',
            'brown adipose tissue': 'adipose tissue|brown adipose tissue',
            'white adipose tissue': 'adipose tissue|white adipose tissue',
            'gonadal adipose tissue': 'adipose tissue|gonadal adipose tissue',
            'mesenteric adipose tissue': 'adipose tissue|mesenteric adipose tissue',
            'subcutaneous adipose tissue': 'adipose tissue|subcutaneous adipose tissue',
            
            # Skin and related
            'skin': 'skin',
            'epidermis': 'skin|epidermis',
            'dermis': 'skin|dermis',
            'hair follicle': 'skin|hair follicle',
            
            # Eye and related
            'eye': 'eye',
            'retina': 'retina',
            'lens': 'lens',
            'cornea': 'cornea',
            
            # Ear and related
            'ear': 'ear',
            'cochlea': 'cochlea',
            
            # Body fluids
            'plasma': 'plasma',
            'serum': 'serum',
            'saliva': 'saliva',
            'urine': 'urine',
            'feces': 'feces',
            'stool': 'feces',
            
            # Extremities
            'forelimb': 'forelimb',
            'fore limb': 'forelimb',
            'hindlimb': 'hindlimb',
            'hind limb': 'hindlimb',
            'tail': 'tail',
            'paw': 'paw',
            
            # Cell types - General
            't cells': 'cells|T cells',
            't cell': 'cells|T cells',
            'primary t cells': 'cells|T cells',
            'primary t cell': 'cells|T cells',
            'myoblast': 'cells|myoblasts',
            'myoblasts': 'cells|myoblasts',
            'microglia': 'cells|microglia',
            'vegetative cell': 'cells|vegetative cells',
            'cell pellet': 'cells|cell pellets',
            'cell pellets': 'cells|cell pellets',
            'primary cell': 'cells|primary cell',
            'skeletal stem cell': 'cells|skeletal stem cells',
            'skeletal stem cells': 'cells|skeletal stem cells',
            
            # Cell types - Epithelial
            'bronchial epithelial cell': 'cells|bronchial epithelial cell',
            'mammary epithelial cell': 'cells|mammary epithelial cells',
            'epithelial cell': 'cells|bronchial epithelial cell',
            
            # Cell types - Cardiac/Cardiovascular
            'cardiomyocyte': 'cells|Human Induced Pluripotent Stem Cell-Derived Cardiomyocytes',
            'cardiovascular progenitor cell': 'cells|cardiovascular progenitor cells',
            'left ventricular cell': 'cells|primary left ventricular cell',
            'primary left ventricular cell': 'cells|primary left ventricular cell',
            
            # Cell types - Fibroblasts
            'lung fibroblast': 'cells|primary lung fibroblast cell',
            'primary lung fibroblast': 'cells|primary lung fibroblast cell',
            'lymphoblastoid cell': 'cells|lymphoblastoid cells',
            
            # Blood cells
            'blood cell': 'blood|blood cells',
            'red blood cell': 'blood|red blood cells',
            'rbc pellet': 'blood|red blood cells',
            'peripheral blood mononuclear cell': 'blood|peripheral blood mononuclear cell',
            'pbmc': 'blood|peripheral blood mononuclear cell',
            'peripheral blood': 'blood|peripheral blood',
            
            # Cell lines
            'cell line': 'cell line',
            'ag01522': 'cell line|Human Fibroblasts AG01522',
            'human fibroblast ag01522': 'cell line|Human Fibroblasts AG01522',
            
            # Cell cultures
            'cell culture': 'cells|cell culture',
            'cultured cell': 'cells|cell culture',
            '3d cell': 'cells|3D cells',
            '3d co-culture': 'cells|3D Co-Culture',
            'harv culture': 'cells|HARV culture',
            'opm chamber culture': 'cells|OPM Chamber Culture',
            'isolated cell': 'cells',
            
            # Microbiology
            'biofilm': 'biofilms',
            'biofilms': 'biofilms',
            'bioaerosol': 'bioaerosol',
            'swab': 'swab',
            'swabs': 'swab',
            'skin swab': 'swab|skin swab',
            'surface swab': 'swab|surface swab',
            'oral swab specimen': 'swab|oral swab specimen',
            
            # Plant tissues
            'root': 'root',
            'shoot': 'shoot',
            'leaf': 'leaf',
            'cotyledon': 'cotyledon',
            'hypocotyl': 'hypocotyl',
            'seed': 'seed',
            'callus': 'callus',
            'plant callus': 'plant callus',
            'callus cell culture': 'plant callus|callus cell culture',
            'hypocotyl cell culture': 'hypocotyl|hypocotyl cell culture',
            'plant stem': 'plant stem',
            'stem': 'plant stem|stem',
            'inflorescence': 'plant stem',
            'rosette': 'plant leaves|rosette',
            'plants': 'plants',
            
            # Fungal structures
            'spore': 'spore',
            'mycelium': 'mycelium',
            
            # Special tissue types
            'cartilage': 'cartilage',
            'engineered cartilage': 'cartilage|human engineered cartilage',
            'tendon': 'tendon',
            
            # Special cell types
            'jurkat t cell': 'jurkat t cells',
            'primary cultured fibroblast cell': 'primary cultured fibroblast cell',
            'primary prostate fibroblast cell culture': 'primary prostate fibroblast cell culture',
        }
        
        keyword_matches = []
        for keyword in anatomical_keywords.keys():
            # Use word boundary matching to avoid partial matches
            # e.g., "ear" shouldn't match "heart"
            keyword_pattern = r'\b' + re.escape(keyword) + r'\b'
            if re.search(keyword_pattern, norm_val):
                keyword_matches.append(keyword)

        if keyword_matches:
            max_match = max(keyword_matches, key=lambda x: len(x.split()))
            # Create hierarchical sub-category under parent
            # Check if target category exists
            child = material_type_grouping
            for cat_part in anatomical_keywords[max_match].split('|'):
                parent = child
                child = OSDRFilterGenerator.get_child_from_parent(cat_part, parent)
                if not child:
                    child = OSDRFilterGenerator.append_new_main_entry(cat_part, parent)
            if norm_val not in child['values']:
                child['values'].append(norm_val)
            return anatomical_keywords[max_match]

        # Fifth: Substring matching with existing values (AFTER keyword matching)
        found_entry = OSDRFilterGenerator.is_value_in_entry_children(norm_val, material_type_grouping, True, True)
        if found_entry:
            found_entry['values'].append(norm_val)
            return '' #TODO Return the parental structure here so it can be recorded among the additions
        
        return None


class OSDRFilterGenerator:
    def __init__(self, input_json_file=None):
        """Initialize and fetch all data
        
        Args:
            input_json_file: Optional path to input JSON file. If None, downloads from OSDR URL.
        """
        print("="*80)
        print("NASA OSDR Filter Options Generator - Final Version")
        print("="*80)
        
        self.base_url = "https://visualization.osdr.nasa.gov/biodata/api/v2"
        self.filter_options_url = "https://osdr.nasa.gov/geode-py/ws/repo/filter-options"
        self.session = requests.Session()
        self.categorizer = SmartCategorizer()
        
        # Load input JSON - either from file or URL
        if input_json_file:
            print(f"\nReading input JSON from file: {input_json_file}")
            self.current_json = self.read_json_file(input_json_file)
        else:
            print(f"\nDownloading current filter-options from OSDR...")
            self.current_json = self.download_current_json()
        
        print("\nFetching API data...")
        self.assay_data = self.fetch_assay_data()
        self.factor_data = self.fetch_factor_data()
        self.organism_data = self.fetch_organism_data()
        self.material_data = self.fetch_material_data()
        self.mission_data = self.fetch_mission_data()
        
        # self.existing_structure = self.extract_existing_structure()
        self.new_json = self.initialize_from_existing()
        
        self.additions = []
        self.unmapped = []
        self.all_osd_ids = set()
    
    def read_json_file(self, filepath):
        """Read JSON from local file"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"  ✓ Loaded successfully")
            return data
        except FileNotFoundError:
            print(f"  ✗ Error: File not found: {filepath}")
            raise
        except json.JSONDecodeError as e:
            print(f"  ✗ Error: Invalid JSON: {e}")
            raise
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            raise
    
    def download_current_json(self):
        """Download current filter-options from OSDR"""
        try:
            response = self.session.get(self.filter_options_url, timeout=30)
            response.raise_for_status()
            data = response.json()
            print(f"  ✓ Downloaded successfully")
            return data
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            raise
    
    def fetch_api_data(self, endpoint, description):
        """Fetch from API"""
        url = f"{self.base_url}/query/assays/{endpoint}"
        print(f"  Fetching {description}...")
        
        try:
            response = self.session.get(url, timeout=60)
            response.raise_for_status()
            data = response.json()
            
            if 'columns' not in data or 'data' not in data:
                raise ValueError(f"Invalid format for {description}")
            
            print(f"    ✓ {len(data['columns'])} columns, {len(data['data'])} rows")
            return data
        except Exception as e:
            print(f"    ✗ Failed: {e}")
            raise
    
    def fetch_assay_data(self):
        """Fetch assay data with measurement, technology, and platform"""
        return self.fetch_api_data(
            "?investigation.study%20assays.study%20assay%20measurement%20type&investigation.study%20assays.study%20assay%20technology%20type&investigation.study%20assays.study%20assay%20technology%20platform&format=json.split",
            "Assay Measurement/Technology/Platform"
        )
    
    def fetch_factor_data(self):
        return self.fetch_api_data(
            "?investigation.study%20assays.study%20assay%20technology%20type&assay.factor%20value&study.factor%20value&schema&format=json.split",
            "Factors"
        )
    
    def fetch_organism_data(self):
        return self.fetch_api_data(
            "?investigation.study%20assays.study%20assay%20technology%20type&study.characteristics.organism&format=json.split",
            "Organisms"
        )
    
    def fetch_material_data(self):
        return self.fetch_api_data(
            "?investigation.study%20assays.study%20assay%20technology%20type&study.characteristics.material%20type&format=json.split",
            "Material Types"
        )
    
    def fetch_mission_data(self):
        return self.fetch_api_data(
            "?investigation.study%20assays.study%20assay%20technology%20type&investigation.study.comment.Project%20Identifier&format=json.split",
            "Missions"
        )
    
    def norm(self, s):
        return self.categorizer.normalize(s)
    
    def extract_existing_structure(self):
        """Extract existing structure from current JSON (handles both old and new formats)"""
        print("\nExtracting existing structure...")
        
        structure = {
            'Project Type': {},
            'Assay technology type': {},
            'Factor': {},
            'Organism': {},
            'Material type': {}
        }
        
        # Check if this is the NEW flat format or OLD nested format
        if 'Assay technology type' in self.current_json:
            # NEW FORMAT: Direct section keys with flat dictionaries
            print("  Detected NEW format (flat sections)")
            for grouping in structure.keys():
                if grouping in self.current_json:
                    for category, values in self.current_json[grouping].items():
                        if category not in structure[grouping]:
                            structure[grouping][category] = set()
                        for val in values:
                            if val:
                                structure[grouping][category].add(val)
        else:
            # OLD FORMAT: Nested 'study' array
            print("  Detected OLD format (nested 'study' array)")
            study_section = self.current_json.get('study', [])
            
            for item in study_section:
                display = item.get('displayValue', '')
                values = item.get('values', [])
                
                grouping = None
                if 'Project Type' in values:
                    grouping = 'Project Type'
                elif 'Assay Type' in display or 'Study Assay Technology Type' in values:
                    grouping = 'Assay technology type'
                elif 'organism' in values:
                    grouping = 'Organism'
                elif 'Tissue' in display or 'material type' in ' '.join(values).lower():
                    grouping = 'Material type'
                elif 'Factor' in display or 'Study Factor Name' in values:
                    grouping = 'Factor'
                
                if not grouping:
                    continue
                
                for child in item.get('children', []):
                    category = child.get('displayValue', child.get('values', [''])[0] if child.get('values') else '')
                    
                    if not category:
                        category = 'Uncategorized'
                    
                    if category not in structure[grouping]:
                        structure[grouping][category] = set()
                    
                    for val in child.get('values', []):
                        if val:
                            structure[grouping][category].add(val)
                    
                    for subchild in child.get('children', []):
                        subcategory = subchild.get('displayValue', subchild.get('values', [''])[0] if subchild.get('values') else '')
                        full_category = f"{category}|{subcategory}"
                        
                        if full_category not in structure[grouping]:
                            structure[grouping][full_category] = set()
                        
                        for val in subchild.get('values', []):
                            if val:
                                structure[grouping][full_category].add(val)
        
        for grouping, categories in structure.items():
            total = sum(len(v) for v in categories.values())
            print(f"  {grouping}: {total} values in {len(categories)} categories")
        
        return structure
    
    def initialize_from_existing(self):
        """Start with all existing values"""
        self.existing_json = []
        new_json = []

        for category in self.current_json.keys():
            for grouping in self.current_json[category]:
                grouping_with_category = copy.deepcopy(grouping)
                grouping_with_category['category'] = category
                self.existing_json.append(copy.deepcopy(grouping_with_category))
                new_json.append(grouping_with_category)
        return new_json

    @staticmethod
    def get_major_grouping(grouping:MainGroup, json_list):
        for entry in json_list:
            if (entry.get('displayValue') == grouping.value or (not entry.get('displayValue') and entry['values'][0] == grouping.value)):
                return entry
        return None

    @staticmethod
    def get_child_from_parent(entry_main_name, parent_entry, search_children=False):
        if 'children' not in parent_entry:
            return None
        for entry in parent_entry['children']:
            if (entry.get('displayValue') == entry_main_name or (not entry.get('displayValue') and entry['values'][0] == entry_main_name)):
                return entry
            if search_children and 'children' in entry:
                child = OSDRFilterGenerator.get_child_from_parent(entry_main_name, entry, True)
                if child:
                    return child
        return None
    
    @staticmethod
    def is_value_in_entry_children(search_value, entry_to_search, search_all_descendants=False, partial_matches=False, ignore_legacy=False):
        if 'children' not in entry_to_search:
            return None
        for entry in entry_to_search['children']:
            for value in entry['values']:
                if search_value == value:
                    return entry
                elif partial_matches and (value in search_value or search_value in value):
                    # Don't match if category has "both sides" in it (legacy category)
                    if ignore_legacy and "both sides" in value:
                        continue
                    if search_value in value:
                        # Only match if lengths are similar (within 20% or less than 5 char difference)
                        len_diff = len(value) - len(search_value)
                        len_ratio = len(search_value) / len(value) if value else 0
                        
                        # Skip match if:
                        # - value is much shorter (< 80% of existing length)
                        # - AND difference is more than 5 characters
                        if len_ratio < 0.8 and len_diff > 5:
                            continue
                    return entry
            # Don't match children if category has "both sides" in it (legacy category)
            if search_all_descendants and (not ignore_legacy or (ignore_legacy and "both_sides" not in value)):
                child_entry = OSDRFilterGenerator.is_value_in_entry_children(search_value, entry, True)
                if child_entry:
                    return child_entry
        return None
    
    @staticmethod
    def append_new_main_entry(entry_main_name, parent_entry):
        if 'children' not in parent_entry:
            parent_entry['children'] = []
        list_to_append = parent_entry['children']
        
        if entry_main_name.islower():
            list_to_append.append({"values":[entry_main_name]})
        else:
            list_to_append.append({"displayValue": entry_main_name, "values": [entry_main_name.lower()]})

        return OSDRFilterGenerator.get_child_from_parent(entry_main_name, parent_entry)

    def process_api_data(self):
        """Process all API data with smart categorization"""
        print("\nProcessing API data...")
        
        # ASSAYS - 3-level hierarchical structure with full capitalization normalization
        print("  Processing assays (measurement -> technology -> platform)...")
        measurement_idx = self.assay_data['columns'].index('investigation.study assays.study assay measurement type')
        technology_idx = self.assay_data['columns'].index('investigation.study assays.study assay technology type')
        platform_idx = self.assay_data['columns'].index('investigation.study assays.study assay technology platform')
        
        # Build canonical mappings for all levels (case-insensitive, first occurrence wins)
        measurement_canonical = {}
        tech_canonical = {}
        platform_canonical = {}
        
        for row in self.assay_data['data']:
            osd_id = row[0]
            measurement = row[measurement_idx]
            technology = row[technology_idx]
            platform = row[platform_idx]
            
            if not measurement or not technology:
                continue
            
            self.all_osd_ids.add(osd_id)
            
            # Determine canonical names (case-insensitive matching)
            # Level 1: Measurement
            if self.norm(measurement) not in measurement_canonical:
                measurement_canonical[self.norm(measurement)] = measurement
            canonical_measurement = measurement_canonical[self.norm(measurement)]
            
            # Level 2: Technology (within this measurement context)
            tech_key = f"{self.norm(canonical_measurement)}|{self.norm(technology)}"
            if tech_key not in tech_canonical:
                tech_canonical[tech_key] = technology
            canonical_tech = tech_canonical[tech_key]
            
            # Level 3: Platform (within this measurement|technology context)
            if platform:
                platform_key = f"{self.norm(canonical_measurement)}|{self.norm(canonical_tech)}|{self.norm(platform)}"
                if platform_key not in platform_canonical:
                    platform_canonical[platform_key] = platform
                canonical_platform = platform_canonical[platform_key]
            
            # Now add using canonical names for categories
            # Level 1: Measurement type
            # Check children for measurement
            measurement_grouping = OSDRFilterGenerator.get_major_grouping(MainGroup.ASSAY_MEASUREMENT, self.new_json)
            if not OSDRFilterGenerator.is_value_in_entry_children(self.norm(measurement), measurement_grouping):
                OSDRFilterGenerator.append_new_main_entry(measurement, measurement_grouping)
                self.additions.append(('Assay Measurement Type', canonical_measurement, measurement))
            if not DEBUG:
                # Level 2: Measurement|Technology (use canonical names)
                technology_grouping = OSDRFilterGenerator.get_major_grouping(MainGroup.ASSAY_TECHNOLOGY, self.new_json)
                measurement_tech_cat = f"{canonical_measurement}|{canonical_tech}"
                if technology not in technology_grouping[measurement_tech_cat]:
                    technology_grouping[measurement_tech_cat].add(technology)
                    self.additions.append(('Assay Technology Type', measurement_tech_cat, technology))
            
                # Level 3: Measurement|Technology|Platform (use canonical names)
                if platform:
                    platform_grouping = OSDRFilterGenerator.get_major_grouping(MainGroup.ASSAY_PLATFORM, self.new_json)
                    measurement_tech_platform_cat = f"{canonical_measurement}|{canonical_tech}|{canonical_platform}"
                    if platform not in platform_grouping[measurement_tech_platform_cat]:
                        platform_grouping[measurement_tech_platform_cat].add(platform)
                        self.additions.append(('Assay Device Platform', measurement_tech_platform_cat, platform))
        
        # FACTORS - Create hierarchical structure
        print("  Processing factors...")
        factor_cols = [col for col in self.factor_data['columns'] if 'factor value' in col.lower()]
        
        # Define parent-child mappings based on manual JSON analysis
        factor_hierarchies = {
            'age': ['age at sample collection', 'age at sample harvest', 'age at start of experiment', 'donor age'],
            'altered gravity': ['altered gravity duration', 'altered gravity simulator'],
            'duration': ['exposure duration', 'hindlimb reloading duration', 'hindlimb unloading duration', 'treatment duration'],
            'ionizing radiation': ['absorbed radiation dose', 'dose', 'ionizing radiation device or source', 
                                   'number of radiation doses', 'particle charge', 'radiation distance', 'time post-irradiation'],
            'preservation method': ['carcass preservation method', 'freezing', 'freezing profile', 
                                   'order of preservation', 'sample preservation method', 
                                   'tissue homogenate preservation time at -80c in rlt buffer', 'tissue preservation method'],
            'time': ['assay time post-irradiation', 'dissection timeline', 'growth time', 'post radiation timepoint',
                    'sample storage time', 'sampling time', 'time of sample collection after euthanasia',
                    'time of sample collection after irradiation', 'time of sample collection after treatment',
                    'time post-irradiation', 'tissue homogenate preservation time at -80c in rlt buffer'],
            'tissue': ['tissue type', 'tissue segment'],
            'treatment': ['bleomycin treatment', 'infection'],
            'weightlessness simulation': ['hindlimb unloading', 'partial weight bearing'],
        }
        
        factor_grouping = OSDRFilterGenerator.get_major_grouping(MainGroup.FACTOR, self.new_json)
        for col in factor_cols:
            factor_name = col.split('.')[-1]
            
            # First check if it exists in current structure (exact match)
            found = False
            if factor_grouping:
                if OSDRFilterGenerator.is_value_in_entry_children(self.norm(factor_name), factor_grouping, True):
                    found = True
            
            if not found:
                # Check if this factor should be a child of a parent
                parent_found = False
                for parent, children in factor_hierarchies.items():
                    if self.norm(factor_name) in [self.norm(c) for c in children]:
                        # Look for parent, if not there, create it
                        parent_entry = OSDRFilterGenerator.get_child_from_parent(parent, factor_grouping)
                        if not parent_entry:
                            parent_entry = OSDRFilterGenerator.append_new_main_entry(parent, factor_grouping)
                        # Add child to parent
                        OSDRFilterGenerator.append_new_main_entry(factor_name, parent_entry)

                        hierarchical_cat = f"{parent}|{factor_name}"
                        self.additions.append(('Factor', hierarchical_cat, factor_name))
                        
                        parent_found = True
                        break
                
                if not parent_found:
                    # Special handling: if factor contains "altered gravity", create sub-category
                    if 'altered gravity' in self.norm(factor_name):
                        hierarchical_cat = f"altered gravity|{factor_name}"

                        # Look for parent, if not there, create it
                        parent_entry = OSDRFilterGenerator.get_child_from_parent('altered gravity', factor_grouping)
                        if not parent_entry:
                            parent_entry = OSDRFilterGenerator.append_new_main_entry(parent, factor_grouping)
                        # Add child to parent
                        OSDRFilterGenerator.append_new_main_entry(factor_name, parent_entry)
                    else:
                        # Put in other|factor_name
                        other_entry = OSDRFilterGenerator.get_child_from_parent('other', factor_grouping)
                        if not other_entry:
                            other_entry = OSDRFilterGenerator.append_new_main_entry('other', factor_grouping)
                        OSDRFilterGenerator.append_new_main_entry(factor_name, other_entry)
                        self.unmapped.append(('Factor', factor_name, 'schema'))

        # ORGANISMS
        print("  Processing organisms...")
        col_idx = self.organism_data['columns'].index('study.characteristics.organism')

        organism_grouping = OSDRFilterGenerator.get_major_grouping(MainGroup.ORGANISM, self.new_json)
        for row in self.organism_data['data']:
            osd_id = row[0]
            organism = row[col_idx]
            
            if not organism:
                continue
            
            self.all_osd_ids.add(osd_id)
            
            # Try exact match first
            found = False
            if organism_grouping:
               if OSDRFilterGenerator.is_value_in_entry_children(self.norm(organism), organism_grouping, True):
                    found = True
            
            # Try taxonomic classification
            if not found:
                taxonomy = self.categorizer.get_taxonomy_category(organism)
                if taxonomy:
                    taxonomy, full_category = taxonomy
                    tax_group = OSDRFilterGenerator.get_child_from_parent(taxonomy, organism_grouping)
                    if not tax_group:
                        tax_group = OSDRFilterGenerator.append_new_main_entry(taxonomy, organism_grouping)
                    OSDRFilterGenerator.append_new_main_entry(organism, tax_group)
                    self.additions.append(('Organism', full_category, organism))
                else:
                    other_orgs = OSDRFilterGenerator.get_child_from_parent('other', organism_grouping)
                    if not other_orgs:
                        other_orgs = OSDRFilterGenerator.append_new_main_entry('other', organism_grouping)
                    OSDRFilterGenerator.append_new_main_entry(organism, other_orgs)
                    self.unmapped.append(('Organism', organism, osd_id))
        
        # MATERIALS
        print("  Processing materials...")
        col_idx = self.material_data['columns'].index('study.characteristics.material type')
        
        for row in self.material_data['data']:
            osd_id = row[0]
            material = row[col_idx]
            
            if not material:
                continue
            
            self.all_osd_ids.add(osd_id)
            material_type_grouping = OSDRFilterGenerator.get_major_grouping(MainGroup.MATERIAL_TYPE, self.new_json)
            
            # Try smart matching
            matched_cat = self.categorizer.match_material_to_existing(material, material_type_grouping)
            
            if matched_cat:
                self.additions.append(('Material type', matched_cat, material))
            elif matched_cat == None:
                    other_materials = OSDRFilterGenerator.get_child_from_parent('other', material_type_grouping)
                    if not other_materials:
                        other_materials = OSDRFilterGenerator.append_new_main_entry('other', material_type_grouping)
                    OSDRFilterGenerator.append_new_main_entry(material, other_materials)
                    self.unmapped.append(('Material type', material, osd_id))

        # MISSIONS
        if not DEBUG:
            print("  Processing missions...")
            col_idx = self.mission_data['columns'].index('investigation.study.comment.project identifier')
            for row in self.mission_data['data']:
                osd_id = row[0]
                missions_str = row[col_idx]
                
                if not missions_str:
                    continue
                
                self.all_osd_ids.add(osd_id)
                missions = [m.strip() for m in missions_str.split(',')]
                
                for mission in missions:
                    if not mission:
                        continue
                    
                    mission_lower = self.norm(mission)
                    
                    if any(x in mission_lower for x in ['expedition', 'increment', 'iss']):
                        category = 'ISS Expeditions'
                    elif any(x in mission_lower for x in ['sts-', 'sts ', 'shuttle', 'sls-']):
                        category = 'Space Shuttle'
                    elif mission.startswith('RR-') or 'rodent research' in mission_lower:
                        category = 'Rodent Research'
                    elif any(x in mission_lower for x in ['bion', 'cosmos']):
                        category = 'Bion/Cosmos'
                    elif any(x in mission_lower for x in ['bric-', 'apex-', 'veg-', 'ffl', 'cbtm', 'cerise']):
                        category = 'Payload Investigations'
                    elif any(x in mission_lower for x in ['ground', 'bsl', 'baseline']):
                        category = 'Ground Control'
                    elif any(x in mission_lower for x in ['gamma_irradiation', 'heavy_ion', 'hze', 'proton_irradiation', 
                                                        'x-ray_irradiation', 'irradiation', 'radiation']):
                        category = 'Radiation Studies'
                    elif any(x in mission_lower for x in ['hindlimb_unloading', 'simulated_microgravity', 
                                                        'simulated_hypergravity', 'simulated_environmental']):
                        category = 'Simulated Conditions'
                    elif any(x in mission_lower for x in ['inspiration4', 'axiom', 'ax-', 'spacex']):
                        category = 'Commercial Spaceflight'
                    else:
                        category = 'Other Missions'
                    
                    if mission not in OSDRFilterGenerator.get_child_from_parent('Mission', self.new_json)[category]:
                        OSDRFilterGenerator.get_child_from_parent('Mission', self.new_json)[category].add(mission)
                        if category == 'Other Missions':
                            self.unmapped.append(('Mission', mission, osd_id))

    def get_all_values(self, json_to_traverse):
        values_set = set()
        if isinstance(json_to_traverse, dict):
            values_set.update(json_to_traverse["values"])
            for value in json_to_traverse.values():
                values_set.update(self.get_all_values(value))
        elif isinstance(json_to_traverse, list):
            for item in json_to_traverse:
                values_set.update(self.get_all_values(item))
        return values_set

    def verify_completeness(self):
        """Verify all original values preserved"""
        print("\n" + "="*80)
        print("VERIFICATION")
        print("="*80)

        original_values = self.get_all_values(self.existing_json)
        new_values = self.get_all_values(self.new_json)
        
        missing = original_values - new_values
        
        print(f"\nOriginal values: {len(original_values)}")
        print(f"New values (excl. Mission): {len(new_values)}")
        print(f"Values added: {len(self.additions)}")
        print(f"Missing: {len(missing)}")

        if missing:
            print(f"\n❌ ERROR: {len(missing)} values missing!")
            for val in sorted(missing)[:20]:
                print(f"  - {val}")
            return False
        else:
            print(f"\n✅ SUCCESS: All preserved + {len(self.additions)} added")
            if not DEBUG:
                mission_total = sum(len(v) for v in OSDRFilterGenerator.get_child_from_parent('Mission', self.new_json).values())
                print(f"📊 Missions: {mission_total} in {len(OSDRFilterGenerator.get_child_from_parent('Mission', self.new_json))} categories")
            print(f"📊 OSD IDs: {len(self.all_osd_ids)}")
            return True
    
    def sort_json(self, json_to_sort):
        if isinstance(json_to_sort, dict):
            if 'children' in json_to_sort and (
                'category' not in json_to_sort or (
                    'category' in json_to_sort and json_to_sort['category'] == 'study'
                )
            ):
                self.sort_json(json_to_sort['children'])
        elif isinstance(json_to_sort, list):
            for item in json_to_sort:
                self.sort_json(item)
            json_to_sort.sort(key=lambda x: (
                str(x['values'][0] == 'other'),
                str(x['values'][0]).lower()
                )
            )

    def generate_output_json(self):
        """Generate final JSON"""
        output = {}
        
        for grouping in ['Project Type', 'Assay technology type', 'Factor', 'Organism', 'Material type', 'Mission']:
            if grouping not in self.new_json:
                continue
            output[grouping] = {}
            for category, values in sorted(self.new_json[grouping].items()):
                output[grouping][category] = sorted(list(values))
        
        return output
    
    def save_outputs(self):
        """Save output files"""
        print("\n" + "="*80)
        print("Saving outputs")
        print("="*80)
        
        # Save JSON
        self.sort_json(self.new_json)
        output_path = os.path.join(os.getcwd(), 'filter-options-new.json')
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.new_json, f)
        print(f"\n✓ JSON: {output_path}")
        
        # Save additions report
        additions_path = os.path.join(os.getcwd(), 'additions-report.txt')
        with open(additions_path, 'w', encoding='utf-8') as f:
            f.write(f"ADDITIONS REPORT\n{'='*80}\n\nTotal: {len(self.additions)}\n{'='*80}\n\n")
            
            if self.additions:
                by_group = defaultdict(lambda: defaultdict(list))
                for grouping, category, value in self.additions:
                    by_group[grouping][category].append(value)
                
                for grouping in sorted(by_group.keys()):
                    f.write(f"\n{grouping}:\n{'-'*80}\n")
                    for category in sorted(by_group[grouping].keys()):
                        f.write(f"\n  {category}:\n")
                        for val in sorted(by_group[grouping][category]):
                            f.write(f"    + {val}\n")
        print(f"✓ Additions: {additions_path}")
        
        # Save unmapped report
        unmapped_path = os.path.join(os.getcwd(), 'unmapped-report.txt')
        with open(unmapped_path, 'w', encoding='utf-8') as f:
            f.write(f"UNMAPPED REPORT\n{'='*80}\n\n")
            
            if self.unmapped:
                by_group = defaultdict(list)
                for grouping, value, osd_id in self.unmapped:
                    by_group[grouping].append((value, osd_id))
                
                for grouping in sorted(by_group.keys()):
                    f.write(f"\n{grouping}:\n{'-'*80}\n")
                    unique = {}
                    for val, osd_id in by_group[grouping]:
                        if val not in unique:
                            unique[val] = []
                        unique[val].append(osd_id)
                    
                    for val in sorted(unique.keys()):
                        osd_list = ', '.join(unique[val][:5])
                        more = len(unique[val]) - 5
                        f.write(f"\n  {val}\n    In: {osd_list}")
                        if more > 0:
                            f.write(f" +{more} more")
                        f.write("\n")
        print(f"✓ Unmapped: {unmapped_path}")
    
    def run(self):
        """Main execution"""
        self.process_api_data()
        is_complete = self.verify_completeness()
        self.save_outputs()
        
        print("\n" + "="*80)
        if is_complete:
            print("✅ COMPLETE")
        else:
            print("❌ INCOMPLETE")
        print("="*80)
        
        return is_complete


def print_usage():
    """Print usage information"""
    print("NASA OSDR Filter Options Generator")
    print()
    print("Usage:")
    print("  python3 osdr_filter_options_generator.py [input.json]")
    print()
    print("Arguments:")
    print("  input.json    Optional. Path to input filter-options JSON file")
    print("                If not provided, downloads from:")
    print("                https://osdr.nasa.gov/geode-py/ws/repo/filter-options")
    print()
    print("Examples:")
    print("  python3 osdr_filter_options_generator.py")
    print("    (Uses OSDR URL as input)")
    print()
    print("  python3 osdr_filter_options_generator.py filter-options-custom.json")
    print("    (Uses local file as input)")
    print()
    print("Output:")
    print("  Creates three files in current directory:")
    print("    - filter-options-new.json")
    print("    - additions-report.txt")
    print("    - unmapped-report.txt")


def main():
    # Check for help
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help', 'help']:
        print_usage()
        sys.exit(0)
    
    # Get optional input file
    input_json_file = None
    if len(sys.argv) > 1:
        input_json_file = sys.argv[1]
        if not os.path.exists(input_json_file):
            print(f"✗ Error: Input file not found: {input_json_file}")
            print()
            print_usage()
            sys.exit(1)
    
    try:
        generator = OSDRFilterGenerator(input_json_file)
        success = generator.run()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
