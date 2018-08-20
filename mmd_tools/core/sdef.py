# -*- coding: utf-8 -*-
import bpy
from mathutils import Vector, Matrix, Quaternion
import numpy as np
import time

class FnSDEF():
    g_verts = {} # global cache
    g_shapekey_data = {}
    g_bone_check = {}
    SHAPEKEY_NAME = 'mmd_sdef_skinning'
    MASK_NAME = 'mmd_sdef_mask'

    def __init__(self):
        raise NotImplementedError('not allowed')

    @classmethod
    def __init_cache(cls, obj, shapekey):
        if hash(obj) not in cls.g_verts:
            key = hash(obj)
            cls.g_verts[key] = cls.__find_vertices(obj)
            cls.g_bone_check[key] = {}
            shapekey_co = np.zeros(len(shapekey.data) * 3, dtype=np.float32)
            shapekey.data.foreach_get('co', shapekey_co)
            shapekey_co = shapekey_co.reshape(len(shapekey.data), 3)
            cls.g_shapekey_data[key] = shapekey_co
            return True
        return False

    @classmethod
    def __check_bone_update(cls, obj, bone0, bone1):
        check = cls.g_bone_check[hash(obj)]
        key = (hash(bone0), hash(bone1))
        if key not in check or (bone0.matrix, bone1.matrix) != check[key]:
            check[key] = (bone0.matrix.copy(), bone1.matrix.copy())
            return True
        return False

    @classmethod
    def __sdef_muted(cls, obj, shapekey):
        mute = shapekey.mute
        if mute != cls.g_bone_check[hash(obj)].get('sdef_mute'):
            mod = obj.modifiers.get('mmd_bone_order_override')
            if mod and mod.type == 'ARMATURE':
                if not mute and cls.MASK_NAME not in obj.vertex_groups:
                    mask = tuple(i[0] for v in cls.g_verts[hash(obj)].values() for i in v[2])
                    obj.vertex_groups.new(name=cls.MASK_NAME).add(mask, 1, 'REPLACE')
                mod.vertex_group = '' if mute else cls.MASK_NAME
                mod.invert_vertex_group = True
                shapekey.vertex_group = cls.MASK_NAME
            cls.g_bone_check[hash(obj)]['sdef_mute'] = mute
        return mute

    @staticmethod
    def has_sdef_data(obj):
        mod = obj.modifiers.get('mmd_bone_order_override')
        if mod and mod.type == 'ARMATURE' and mod.object:
            kb = getattr(obj.data.shape_keys, 'key_blocks', None)
            return kb and 'mmd_sdef_c' in kb and 'mmd_sdef_r0' in kb and 'mmd_sdef_r1' in kb
        return False

    @classmethod
    def __find_vertices(cls, obj):
        if not cls.has_sdef_data(obj):
            return {}

        vertices = {}
        pose_bones = obj.modifiers.get('mmd_bone_order_override').object.pose.bones
        bone_map = {g.index:pose_bones[g.name] for g in obj.vertex_groups if g.name in pose_bones}
        sdef_c = obj.data.shape_keys.key_blocks['mmd_sdef_c'].data
        sdef_r0 = obj.data.shape_keys.key_blocks['mmd_sdef_r0'].data
        sdef_r1 = obj.data.shape_keys.key_blocks['mmd_sdef_r1'].data
        vd = obj.data.vertices

        for i in range(len(sdef_c)):
            if vd[i].co != sdef_c[i].co:
                bgs = [g for g in vd[i].groups if g.group in bone_map and g.weight] # bone groups
                if len(bgs) >= 2:
                    bgs.sort(key=lambda x: x.group)
                    # preprocessing
                    w0, w1 = bgs[0].weight, bgs[1].weight
                    # w0 + w1 == 1
                    w0 = w0 / (w0 + w1)
                    w1 = 1 - w0

                    c, r0, r1 = sdef_c[i].co, sdef_r0[i].co, sdef_r1[i].co
                    rw = r0 * w0 + r1 * w1
                    r0 = c + r0 - rw
                    r1 = c + r1 - rw

                    key = (hash(bone_map[bgs[0].group]), hash(bone_map[bgs[1].group]))
                    if key not in vertices:
                        vertices[key] = (bone_map[bgs[0].group], bone_map[bgs[1].group], [], [])
                    vertices[key][2].append((i, w0, w1, vd[i].co-c, (c+r0)/2, (c+r1)/2))
                    vertices[key][3].append(i)
        return vertices

    @classmethod
    def driver_function_wrap(cls, obj_name, bulk_update, use_skip, use_scale):
        obj = bpy.data.objects[obj_name]
        shapekey = obj.data.shape_keys.key_blocks[cls.SHAPEKEY_NAME]
        return cls.driver_function(shapekey, obj_name, bulk_update, use_skip, use_scale)

    @classmethod
    def driver_function(cls, shapekey, obj_name, bulk_update, use_skip, use_scale):
        obj = bpy.data.objects[obj_name]
        cls.__init_cache(obj, shapekey)
        if cls.__sdef_muted(obj, shapekey):
            return 0.0

        if not bulk_update:
            shapekey_data = shapekey.data
            if use_scale:
                # with scale
                for bone0, bone1, sdef_data, vids in cls.g_verts[hash(obj)].values():
                    if use_skip and not cls.__check_bone_update(obj, bone0, bone1):
                        continue
                    mat0 = bone0.matrix * bone0.bone.matrix_local.inverted()
                    mat1 = bone1.matrix * bone1.bone.matrix_local.inverted()
                    rot0 = mat0.to_quaternion()
                    rot1 = mat1.to_quaternion()
                    if rot1.dot(rot0) < 0:
                        rot1 = -rot1
                    s0, s1 = mat0.to_scale(), mat1.to_scale()
                    for vid, w0, w1, pos_c, cr0, cr1 in sdef_data:
                        mat_rot = (rot0*w0 + rot1*w1).normalized().to_matrix()
                        s = s0*w0 + s1*w1
                        mat_rot *= Matrix([[s[0],0,0], [0,s[1],0], [0,0,s[2]]])
                        shapekey_data[vid].co = mat_rot * pos_c + mat0 * cr0 * w0 + mat1 * cr1 * w1
            else:
                # default
                for bone0, bone1, sdef_data, vids in cls.g_verts[hash(obj)].values():
                    if use_skip and not cls.__check_bone_update(obj, bone0, bone1):
                        continue
                    mat0 = bone0.matrix * bone0.bone.matrix_local.inverted()
                    mat1 = bone1.matrix * bone1.bone.matrix_local.inverted()
                    rot0 = mat0.to_quaternion()
                    rot1 = mat1.to_quaternion()
                    if rot1.dot(rot0) < 0:
                        rot1 = -rot1
                    for vid, w0, w1, pos_c, cr0, cr1 in sdef_data:
                        mat_rot = (rot0*w0 + rot1*w1).normalized().to_matrix()
                        shapekey_data[vid].co = mat_rot * pos_c + mat0 * cr0 * w0 + mat1 * cr1 * w1
        else: # bulk update
            shapekey_data = cls.g_shapekey_data[hash(obj)]
            if use_scale:
                # scale & bulk update
                for bone0, bone1, sdef_data, vids in cls.g_verts[hash(obj)].values():
                    if use_skip and not cls.__check_bone_update(obj, bone0, bone1):
                        continue
                    mat0 = bone0.matrix * bone0.bone.matrix_local.inverted()
                    mat1 = bone1.matrix * bone1.bone.matrix_local.inverted()
                    rot0 = mat0.to_quaternion()
                    rot1 = mat1.to_quaternion()
                    if rot1.dot(rot0) < 0:
                        rot1 = -rot1
                    s0, s1 = mat0.to_scale(), mat1.to_scale()
                    def scale(mat_rot, w0, w1):
                        s = s0*w0 + s1*w1
                        return mat_rot * Matrix([[s[0],0,0], [0,s[1],0], [0,0,s[2]]])
                    shapekey_data[vids] = [scale((rot0*w0 + rot1*w1).normalized().to_matrix(), w0, w1) * pos_c + mat0 * cr0 * w0 + mat1 * cr1 * w1 for vid, w0, w1, pos_c, cr0, cr1 in sdef_data]
            else:
                # bulk update
                for bone0, bone1, sdef_data, vids in cls.g_verts[hash(obj)].values():
                    if use_skip and not cls.__check_bone_update(obj, bone0, bone1):
                        continue
                    mat0 = bone0.matrix * bone0.bone.matrix_local.inverted()
                    mat1 = bone1.matrix * bone1.bone.matrix_local.inverted()
                    rot0 = mat0.to_quaternion()
                    rot1 = mat1.to_quaternion()
                    if rot1.dot(rot0) < 0:
                        rot1 = -rot1
                    shapekey_data[vids] = [(rot0*w0 + rot1*w1).normalized().to_matrix() * pos_c + mat0 * cr0 * w0 + mat1 * cr1 * w1 for vid, w0, w1, pos_c, cr0, cr1 in sdef_data]
            shapekey.data.foreach_set('co', shapekey_data.reshape(3 * len(shapekey.data)))

        return 1.0 # shapkey value

    @classmethod
    def register_driver_function(cls):
        if 'mmd_sdef_driver' not in bpy.app.driver_namespace:
            bpy.app.driver_namespace['mmd_sdef_driver'] = cls.driver_function
        if 'mmd_sdef_driver_wrap' not in bpy.app.driver_namespace:
            bpy.app.driver_namespace['mmd_sdef_driver_wrap'] = cls.driver_function_wrap

    BENCH_LOOP=10
    @classmethod
    def __get_benchmark_result(cls, obj, shapkey, use_scale, use_skip):
        # warmed up
        cls.driver_function(shapkey, obj.name, bulk_update=True, use_skip=False, use_scale=use_scale)
        cls.driver_function(shapkey, obj.name, bulk_update=False, use_skip=False, use_scale=use_scale)
        # benchmark
        t = time.time()
        for i in range(cls.BENCH_LOOP):
            cls.driver_function(shapkey, obj.name, bulk_update=False, use_skip=False, use_scale=use_scale)
        default_time = time.time() - t
        t = time.time()
        for i in range(cls.BENCH_LOOP):
            cls.driver_function(shapkey, obj.name, bulk_update=True, use_skip=False, use_scale=use_scale)
        bulk_time = time.time() - t
        result = default_time > bulk_time
        print('FnSDEF:benchmark: default %.4f vs bulk_update %.4f => bulk_update=%s' % (default_time, bulk_time, result))
        return result

    @classmethod
    def bind(cls, obj, bulk_update=None, use_skip=True, use_scale=False):
        # Unbind first
        cls.unbind(obj)
        if not cls.has_sdef_data(obj):
            return False
        # Create the shapekey for the driver
        shapekey = obj.shape_key_add(name=cls.SHAPEKEY_NAME, from_mix=False)
        cls.__init_cache(obj, obj.data.shape_keys.key_blocks[cls.SHAPEKEY_NAME])
        cls.register_driver_function()
        if bulk_update is None:
            bulk_update = cls.__get_benchmark_result(obj, shapekey, use_scale, use_skip)
        # Add the driver to the shapekey
        f = obj.data.shape_keys.driver_add('key_blocks["'+cls.SHAPEKEY_NAME+'"].value', -1)
        f.driver.show_debug_info = False
        f.driver.type = 'SCRIPTED'
        ov = f.driver.variables.new()
        ov.name = 'obj'
        ov.type = 'SINGLE_PROP'
        ov.targets[0].id = obj
        ov.targets[0].data_path = 'name'
        if hasattr(f.driver, 'use_self'): # Blender 2.78+
            f.driver.use_self = True
            param = (bulk_update, use_skip, use_scale)
            f.driver.expression = 'mmd_sdef_driver(self, obj, bulk_update={}, use_skip={}, use_scale={})'.format(*param)
        else:
            param = (obj.name, bulk_update, use_skip, use_scale)
            f.driver.expression = 'mmd_sdef_driver_wrap("{}", bulk_update={}, use_skip={}, use_scale={})'.format(*param)
        return True

    @classmethod
    def unbind(cls, obj):
        from mmd_tools.bpyutils import ObjectOp
        if obj.data.shape_keys:
            if obj.data.shape_keys.animation_data:
                for d in obj.data.shape_keys.animation_data.drivers:
                    if cls.SHAPEKEY_NAME in d.data_path:
                        obj.data.shape_keys.driver_remove(d.data_path, -1)
            if cls.SHAPEKEY_NAME in obj.data.shape_keys.key_blocks:
                ObjectOp(obj).shape_key_remove(obj.data.shape_keys.key_blocks[cls.SHAPEKEY_NAME])
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.vertex_group == cls.MASK_NAME:
                mod.vertex_group = ''
                mod.invert_vertex_group = False
                break
        if cls.MASK_NAME in obj.vertex_groups:
            obj.vertex_groups.remove(obj.vertex_groups[cls.MASK_NAME])
        cls.clear_cache(obj)

    @classmethod
    def clear_cache(cls, obj=None, unused_only=False):
        if unused_only:
            valid_keys = set(hash(i) for i in bpy.data.objects if i.type == 'MESH' and i != obj)
            for key in (cls.g_verts.keys()-valid_keys):
                del cls.g_verts[key]
            for key in (cls.g_shapekey_data.keys()-cls.g_verts.keys()):
                del cls.g_shapekey_data[key]
            for key in (cls.g_bone_check.keys()-cls.g_verts.keys()):
                del cls.g_bone_check[key]
        elif obj:
            key = hash(obj)
            if key in cls.g_verts:
                del cls.g_verts[key]
            if key in cls.g_shapekey_data:
                del cls.g_shapekey_data[key]
            if key in cls.g_bone_check:
                del cls.g_bone_check[key]
        else:
            cls.g_verts = {}
            cls.g_bone_check = {}
            cls.g_shapekey_data = {}
